#include "index_expand.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <exception>
#include <functional>
#include <limits>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <tuple>
#include <unordered_map>
#include <utility>

namespace n2scftdb {
namespace {

using Exponent = std::int64_t;
// A symbol encodes (symbol, character power). Only nonzero powers occur.
// symbol is character_index * max_adams + (adams_power - 1)
using CharacterProduct = std::vector<std::pair<std::uint64_t, Exponent>>;

struct Monomial {
    Exponent y = 0;
    Exponent u = 0;
    CharacterProduct characters;

    bool operator==(const Monomial& other) const {
        return y == other.y && u == other.u && characters == other.characters;
    }
};

struct MonomialHash {
    std::size_t operator()(const Monomial& monomial) const {
        std::size_t seed = 0;
        const auto combine = [&seed](std::uint64_t value) {
            seed ^= std::hash<std::uint64_t>{}(value) + 0x9e3779b97f4a7c15ULL
                    + (seed << 6) + (seed >> 2);
        };
        combine(static_cast<std::uint64_t>(monomial.y));
        combine(static_cast<std::uint64_t>(monomial.u));
        for (const auto& [symbol, power] : monomial.characters) {
            combine(symbol);
            combine(static_cast<std::uint64_t>(power));
        }
        return seed;
    }
};

using Polynomial = std::unordered_map<Monomial, mpq_class, MonomialHash>;

// One deadline per expansion, shared read-only except for cancellation. Clock
// reads and the atomic cancellation load occur only at sampled checkpoints.
class ExpansionDeadline {
public:
    using Clock = std::chrono::steady_clock;
    explicit ExpansionDeadline(double seconds) : seconds_(seconds) {
        if (!std::isfinite(seconds)) {
            throw std::invalid_argument("index expansion timeout must be finite");
        }
        if (enabled()) start_ = Clock::now();
    }
    bool enabled() const { return seconds_ >= 0; }
    Clock::time_point check() {
        if (!enabled()) return {};
        const auto current = Clock::now();
        if (expired_.load(std::memory_order_relaxed) || remaining(current) <= 0) {
            expired_.store(true, std::memory_order_relaxed);
            throw std::runtime_error("index expansion timed out");
        }
        return current;
    }
    double remaining(Clock::time_point current) const {
        return seconds_ - std::chrono::duration<double>(current - start_).count();
    }
private:
    double seconds_;
    Clock::time_point start_{};
    std::atomic<bool> expired_{false};
};

// Private polling state per serial stage or worker task: no shared writes in
// the hot loops. Target a clock read every millisecond, with bounded growth and
// shorter batches near expiration. Zero budget is the unlimited fast path.
class DeadlinePoll {
public:
    explicit DeadlinePoll(ExpansionDeadline& deadline) : deadline_(deadline) {
        if (deadline.enabled()) {
            previous_ = deadline.check();
            interval_ = budget_ = 64;
        }
    }
    void poll() {
        if (budget_ && --budget_ == 0) {
            const auto current = deadline_.check();
            const double elapsed = std::chrono::duration<double>(current - previous_).count();
            const double target = std::min(0.001, deadline_.remaining(current) * 0.25);
            const double estimate = elapsed > 0 ? interval_ * target / elapsed : 4096;
            const auto next = estimate >= 4096 ? 4096U : estimate < 1 ? 1U
                            : static_cast<unsigned int>(estimate);
            interval_ = budget_ = std::min(next, interval_ * 4);
            previous_ = current;
        }
    }
    ExpansionDeadline& deadline() const { return deadline_; }
private:
    ExpansionDeadline& deadline_;
    ExpansionDeadline::Clock::time_point previous_{};
    unsigned int interval_ = 0, budget_ = 0;
};

// A call-local pool. The caller participates as worker 0, so the requested
// count includes it. No global pool, shared GMP accumulator, or nested pool.
class Workers {
public:
    explicit Workers(std::size_t count) : count_(count) {
        try {
            threads_.reserve(count - 1);
            for (std::size_t id = 1; id < count; ++id) {
                threads_.emplace_back([this, id] { worker_loop(id); });
            }
        } catch (...) {
            stop();
            throw;
        }
    }

    ~Workers() { stop(); }
    Workers(const Workers&) = delete;
    Workers& operator=(const Workers&) = delete;

    std::size_t size() const { return count_; }

    void run(std::size_t task_count,
             std::function<void(std::size_t, std::size_t)> function) {
        if (task_count == 1) {
            function(0, 0);
            return;
        }
        {
            std::lock_guard<std::mutex> lock(mutex_);
            function_ = std::move(function);
            task_count_ = task_count;
            next_.store(0, std::memory_order_relaxed);
            cancelled_.store(false, std::memory_order_relaxed);
            error_ = nullptr;
            pending_ = threads_.size();
            ++generation_;
        }
        ready_.notify_all();
        consume(0);
        std::unique_lock<std::mutex> lock(mutex_);
        done_.wait(lock, [this] { return pending_ == 0; });
        function_ = {};
        if (error_) {
            std::rethrow_exception(error_);
        }
    }

private:
    void consume(std::size_t id) {
        try {
            while (!cancelled_.load(std::memory_order_relaxed)) {
                const auto task = next_.fetch_add(1, std::memory_order_relaxed);
                if (task >= task_count_) {
                    break;
                }
                function_(id, task);
            }
        } catch (...) {
            cancelled_.store(true, std::memory_order_relaxed);
            std::lock_guard<std::mutex> lock(mutex_);
            if (!error_) {
                error_ = std::current_exception();
            }
        }
    }

    void worker_loop(std::size_t id) {
        std::size_t observed_generation = 0;
        for (;;) {
            std::unique_lock<std::mutex> lock(mutex_);
            ready_.wait(lock, [this, observed_generation] {
                return stopping_ || generation_ != observed_generation;
            });
            if (stopping_) {
                return;
            }
            observed_generation = generation_;
            lock.unlock();
            consume(id);
            lock.lock();
            if (--pending_ == 0) {
                done_.notify_one();
            }
        }
    }

    void stop() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            stopping_ = true;
        }
        ready_.notify_all();
        for (auto& thread : threads_) {
            thread.join();
        }
    }

    std::size_t count_;
    std::vector<std::thread> threads_;
    std::mutex mutex_;
    std::condition_variable ready_, done_;
    std::function<void(std::size_t, std::size_t)> function_;
    std::atomic<std::size_t> next_{0};
    std::atomic<bool> cancelled_{false};
    std::size_t task_count_ = 0, pending_ = 0, generation_ = 0;
    bool stopping_ = false;
    std::exception_ptr error_;
};

Exponent checked_add(Exponent a, Exponent b) {
    if ((b > 0 && a > std::numeric_limits<Exponent>::max() - b)
        || (b < 0 && a < std::numeric_limits<Exponent>::min() - b)) {
        throw std::overflow_error("index expansion exponent overflow");
    }
    return a + b;
}

Monomial multiply(const Monomial& a, const Monomial& b) {
    Monomial result{checked_add(a.y, b.y), checked_add(a.u, b.u), {}};
    result.characters.reserve(a.characters.size() + b.characters.size());
    auto left = a.characters.begin();
    auto right = b.characters.begin();
    while (left != a.characters.end() && right != b.characters.end()) {
        if (left->first < right->first) {
            result.characters.push_back(*left++);
        } else if (right->first < left->first) {
            result.characters.push_back(*right++);
        } else {
            result.characters.emplace_back(left->first,
                                           checked_add(left->second, right->second));
            ++left;
            ++right;
        }
    }
    result.characters.insert(result.characters.end(), left, a.characters.end());
    result.characters.insert(result.characters.end(), right, b.characters.end());
    return result;
}

void accumulate(Polynomial& polynomial, Monomial monomial, const mpq_class& value) {
    if (value == 0) {
        return;
    }
    auto [position, inserted] = polynomial.try_emplace(std::move(monomial), value);
    if (!inserted) {
        position->second += value;
        if (position->second == 0) {
            polynomial.erase(position);
        }
    }
}

// Move existing nodes instead of allocating/copying their keys and rationals.
// No other worker may access either map during this reduction.
void merge_polynomials(Polynomial& target, Polynomial& source, DeadlinePoll& poll) {
    if (target.size() < source.size()) {
        target.swap(source);
    }
    while (!source.empty()) {
        poll.poll();
        auto inserted = target.insert(source.extract(source.begin()));
        if (!inserted.inserted) {
            inserted.position->second += inserted.node.mapped();
            if (inserted.position->second == 0) {
                target.erase(inserted.position);
            }
        }
    }
}

struct ProductTask {
    const Polynomial::value_type* left;
    Polynomial::const_iterator begin;
    Polynomial::const_iterator end;
};

// A saturating work estimate avoids overflow for large sparse buckets.
bool worth_parallelizing(std::size_t degree, const std::vector<Polynomial>& derivative,
                        const std::vector<Polynomial>& expansion) {
    constexpr std::size_t minimum_products = 32768;
    std::size_t remaining = minimum_products;
    for (std::size_t k = 2; k <= degree; ++k) {
        const auto left = derivative[k].size();
        const auto right = expansion[degree - k].size();
        if (left == 0 || right == 0) {
            continue;
        }
        if (left > remaining / right || left * right >= remaining) {
            return true;
        }
        remaining -= left * right;
    }
    return false;
}

Polynomial parallel_degree(std::size_t degree, const std::vector<Polynomial>& derivative,
                           const std::vector<Polynomial>& expansion, int worker_count,
                           std::unique_ptr<Workers>& workers, DeadlinePoll& poll) {
    constexpr std::size_t chunk_size = 512;
    std::vector<ProductTask> tasks;
    for (std::size_t k = 2; k <= degree; ++k) {
        const auto& right = expansion[degree - k];
        if (derivative[k].empty()) {
            continue;
        }
        // Split the right operand too: even a single derivative monomial
        // can have a large product. Iterators remain valid until the barrier.
        for (auto begin = right.begin(); begin != right.end();) {
            poll.poll();
            auto end = begin;
            for (std::size_t i = 0; i < chunk_size && end != right.end(); ++i) {
                ++end;
            }
            for (const auto& left : derivative[k]) {
                poll.poll();
                tasks.push_back({&left, begin, end});
            }
            begin = end;
        }
    }
    if (!workers) {
        workers = std::make_unique<Workers>(
            std::min(static_cast<std::size_t>(worker_count), tasks.size()));
    }
    std::vector<Polynomial> partials(workers->size());
    workers->run(tasks.size(), [&](std::size_t id, std::size_t task_index) {
        DeadlinePoll local_poll(poll.deadline());
        const auto& task = tasks[task_index];
        for (auto right = task.begin; right != task.end; ++right) {
            local_poll.poll();
            const mpq_class coefficient = task.left->second * right->second;
            accumulate(partials[id], multiply(task.left->first, right->first), coefficient);
        }
    });

    // Pairwise reductions operate on disjoint maps at each barrier. Move into
    // the larger map in each pair to reduce hashing, allocations, and rehashes.
    std::vector<std::size_t> active;
    for (std::size_t i = 0; i < partials.size(); ++i) {
        if (!partials[i].empty()) {
            active.push_back(i);
        }
    }
    while (active.size() > 1) {
        workers->run(active.size() / 2, [&](std::size_t, std::size_t pair) {
            DeadlinePoll local_poll(poll.deadline());
            merge_polynomials(partials[active[2 * pair]], partials[active[2 * pair + 1]], local_poll);
        });
        std::vector<std::size_t> next;
        for (std::size_t i = 0; i < active.size(); i += 2) {
            next.push_back(active[i]);
        }
        active = std::move(next);
    }
    return active.empty() ? Polynomial{} : std::move(partials[active[0]]);
}

CharacterProduct character_product(const IndexedMonomial& indices,
                                   int adams, int max_adams, DeadlinePoll& poll) {
    IndexedMonomial sorted = indices;
    std::sort(sorted.begin(), sorted.end(), [&](int a, int b) {
        poll.poll();
        return a < b;
    });
    CharacterProduct result;
    for (int index : sorted) {
        poll.poll();
        const auto symbol = static_cast<std::uint64_t>(index) * max_adams + adams - 1;
        if (!result.empty() && result.back().first == symbol) {
            result.back().second = checked_add(result.back().second, 1);
        } else {
            result.emplace_back(symbol, 1);
        }
    }
    return result;
}

struct Letter {
    int t;
    int y;
    int u;
    int coefficient;
};

constexpr std::array<Letter, 5> vector_letters{{
    {2, 0, 2, 1}, {4, 0, -2, -1}, {3, 1, 0, -1},
    {3, -1, 0, -1}, {6, 0, 0, 2},
}};
constexpr std::array<Letter, 2> hyper_letters{{
    {2, 0, -1, 1}, {4, 0, 1, -1},
}};

// Accumulate E_k = k*A_k, where A = sum_j letters(t^j,y^j,u^j,C(j))/j.
// The denominator J is also Adams transformed: each derivative contributes
// t^(3*j)*y^(+/-j). Bound derivative sums before creating any monomials.
template <std::size_t Count>
void add_letters(std::vector<Polynomial>& derivative, int adams,
                 const IndexedMonomial& indices, const mpz_class& multiplicity,
                 const std::array<Letter, Count>& letters, DeadlinePoll& poll) {
    if (multiplicity == 0) {
        return;
    }
    const int order = static_cast<int>(derivative.size() - 1);
    const auto characters = character_product(indices, adams, order / 2, poll);
    for (const auto& letter : letters) {
        const int remaining = order / adams - letter.t;
        if (remaining < 0) {
            continue;
        }
        // k/j = letter.t + 3*(a+b) is integral, so E has integer coefficients.
        // Enumerate the two independent derivative geometric series.
        for (int sum = 0; sum <= remaining / 3; ++sum) {
            const int unscaled_degree = letter.t + 3 * sum;
            const int degree = adams * unscaled_degree;  // bounded by order
            mpq_class coefficient(multiplicity);
            coefficient *= letter.coefficient;
            coefficient *= unscaled_degree;
            for (int a = 0; a <= sum; ++a) {
                poll.poll();
                Monomial monomial{
                    static_cast<Exponent>(adams) * (letter.y + 2LL * a - sum),
                    static_cast<Exponent>(adams) * letter.u,
                    characters,
                };
                accumulate(derivative[degree], std::move(monomial), coefficient);
            }
        }
    }
}

std::vector<CharacterPowers> unpack_characters(const CharacterProduct& product,
                                              int max_adams) {
    std::vector<CharacterPowers> result;
    std::size_t begin = 0;
    while (begin < product.size()) {
        const auto character = product[begin].first / max_adams;
        Exponent weight = 0;
        std::size_t end = begin;
        for (; end < product.size() && product[end].first / max_adams == character;
             ++end) {
            const auto adams = static_cast<Exponent>(product[end].first % max_adams + 1);
            if (product[end].second > std::numeric_limits<Exponent>::max() / adams) {
                throw std::overflow_error("character Adams weight overflow");
            }
            weight = checked_add(weight, adams * product[end].second);
        }
        CharacterPowers powers{static_cast<int>(character), {}};
        if (static_cast<std::uint64_t>(weight) > powers.adams_powers.max_size()) {
            throw std::length_error("character Adams tuple is too large");
        }
        powers.adams_powers.resize(static_cast<std::size_t>(weight), 0);
        for (std::size_t i = begin; i < end; ++i) {
            powers.adams_powers[product[i].first % max_adams] = product[i].second;
        }
        result.push_back(std::move(powers));
        begin = end;
    }
    return result;
}

struct OutputTask {
    Polynomial::iterator begin;
    Polynomial::iterator end;
    int degree;
    std::size_t offset;
};

std::vector<IndexFormTerm> construct_output(std::vector<Polynomial>& expansion,
                                          int max_adams, int worker_count,
                                          std::unique_ptr<Workers>& workers, DeadlinePoll& poll) {
    std::vector<IndexFormTerm> terms;
    std::size_t term_count = 0;
    for (const auto& polynomial : expansion) {
        poll.poll();
        if (polynomial.size() > terms.max_size() - term_count) {
            throw std::length_error("index output has too many terms");
        }
        term_count += polynomial.size();
    }

    constexpr std::size_t minimum_parallel_terms = 4096;
    if (worker_count == 1 || term_count < minimum_parallel_terms) {
        // GMP rational moves are not noexcept, so vector growth can copy all
        // preceding coefficients. Reserve once before moving any terms.
        terms.reserve(term_count);
        for (std::size_t degree = 0; degree < expansion.size(); ++degree) {
            for (auto& [monomial, coefficient] : expansion[degree]) {
                poll.poll();
                terms.push_back({std::move(coefficient), static_cast<int>(degree),
                                 monomial.y, monomial.u,
                                 unpack_characters(monomial.characters, max_adams)});
            }
            expansion[degree].clear();
        }
        return terms;
    }

    constexpr std::size_t chunk_size = 512;
    std::vector<OutputTask> tasks;
    std::size_t offset = 0;
    for (std::size_t degree = 0; degree < expansion.size(); ++degree) {
        auto& polynomial = expansion[degree];
        for (auto begin = polynomial.begin(); begin != polynomial.end();) {
            poll.poll();
            auto end = begin;
            std::size_t count = 0;
            while (count < chunk_size && end != polynomial.end()) {
                ++end;
                ++count;
            }
            tasks.push_back({begin, end, static_cast<int>(degree), offset});
            offset += count;
            begin = end;
        }
    }
    // Construct every vector element before workers write distinct slots.
    // Map structure stays fixed while they move distinct rational values.
    terms.resize(term_count);
    if (!workers) {
        workers = std::make_unique<Workers>(
            std::min(static_cast<std::size_t>(worker_count), tasks.size()));
    }
    workers->run(tasks.size(), [&](std::size_t, std::size_t task_index) {
        DeadlinePoll local_poll(poll.deadline());
        const auto& task = tasks[task_index];
        auto destination = task.offset;
        for (auto source = task.begin; source != task.end; ++source) {
            local_poll.poll();
            auto& term = terms[destination++];
            term.coefficient = std::move(source->second);
            term.t_power = task.degree;
            term.y_power = source->first.y;
            term.u_power = source->first.u;
            term.characters = unpack_characters(source->first.characters, max_adams);
        }
    });
    // The barrier has completed: no worker retains a live map access.
    for (auto& polynomial : expansion) {
        polynomial.clear();
    }
    return terms;
}

bool term_less(const IndexFormTerm& a, const IndexFormTerm& b) {
    const auto a_powers = std::tie(a.t_power, a.y_power, a.u_power);
    const auto b_powers = std::tie(b.t_power, b.y_power, b.u_power);
    if (a_powers != b_powers) {
        return a_powers < b_powers;
    }
    return std::lexicographical_compare(
        a.characters.begin(), a.characters.end(), b.characters.begin(), b.characters.end(),
        [](const CharacterPowers& left, const CharacterPowers& right) {
            return std::tie(left.character, left.adams_powers)
                   < std::tie(right.character, right.adams_powers);
        });
}

// Shared implementation keeps one deadline across expansion and optional sort.
std::vector<IndexFormTerm> expand_index_unordered_impl(
    int order, int character_count, const std::vector<int>& vector_characters,
    const MatterMultiplicities& matter_multiplicities, int worker_count,
    ExpansionDeadline& deadline) {
    if (worker_count <= 0) {
        throw std::invalid_argument("worker_count must be positive");
    }
    if (order < 0 || character_count < 0) {
        throw std::invalid_argument("order and character_count must be nonnegative");
    }
    DeadlinePoll poll(deadline);
    const auto validate_index = [character_count](int index) {
        if (index < 0 || index >= character_count) {
            throw std::invalid_argument("character index is outside [0, character_count)");
        }
    };
    for (int index : vector_characters) {
        poll.poll();
        validate_index(index);
    }
    for (const auto& [indices, multiplicity] : matter_multiplicities) {
        poll.poll();
        (void)multiplicity;
        for (int index : indices) {
            poll.poll();
            validate_index(index);
        }
    }
    if (order < 2) {
        deadline.check();
        return {{mpq_class(1), 0, 0, 0, {}}};
    }

    const int max_adams = order / 2;
    const auto degree_count = static_cast<std::size_t>(order) + 1;
    std::vector<Polynomial> derivative(degree_count);
    for (int adams = 1; adams <= max_adams; ++adams) {
        deadline.check();
        for (int character : vector_characters) {
            add_letters(derivative, adams, {character}, mpz_class(1), vector_letters, poll);
        }
        for (const auto& [indices, multiplicity] : matter_multiplicities) {
            add_letters(derivative, adams, indices, multiplicity, hyper_letters, poll);
        }
    }

    // B=exp(A), t*B'=(t*A')*B:
    // B_0=1, B_n=(1/n)*sum_{k=2}^n E_k*B_(n-k).
    // t-degree is implicit in each bucket; no discarded high-degree products
    // are ever formed. Formal characters commute but are not decomposed.
    std::vector<Polynomial> expansion(degree_count);
    expansion[0].emplace(Monomial{}, mpq_class(1));
    std::unique_ptr<Workers> workers;
    for (std::size_t n = 2; n < degree_count; ++n) {
        deadline.check();
        auto& result = expansion[n];
        if (worker_count > 1 && worth_parallelizing(n, derivative, expansion)) {
            result = parallel_degree(n, derivative, expansion, worker_count, workers, poll);
        } else {
            for (std::size_t k = 2; k <= n; ++k) {
                if (expansion[n - k].empty()) {
                    continue;
                }
                for (const auto& [left, left_coefficient] : derivative[k]) {
                    for (const auto& [right, right_coefficient] : expansion[n - k]) {
                        poll.poll();
                        const mpq_class coefficient = left_coefficient * right_coefficient;
                        accumulate(result, multiply(left, right), coefficient);
                    }
                }
            }
        }
        for (auto& entry : result) {
            poll.poll();
            entry.second /= static_cast<unsigned long>(n);
        }
    }
    deadline.check();
    auto terms = construct_output(expansion, max_adams, worker_count, workers, poll);
    workers.reset();
    deadline.check();
    return terms;
}

}  // namespace

std::vector<IndexFormTerm> expand_index(
    int order, int character_count, const std::vector<int>& vector_characters,
    const MatterMultiplicities& matter_multiplicities) {
    return expand_index(order, character_count, vector_characters, matter_multiplicities, 1, 600.0);
}

std::vector<IndexFormTerm> expand_index(
    int order, int character_count, const std::vector<int>& vector_characters,
    const MatterMultiplicities& matter_multiplicities, int worker_count) {
    return expand_index(order, character_count, vector_characters, matter_multiplicities,
                        worker_count, 600.0);
}

std::vector<IndexFormTerm> expand_index(
    int order, int character_count, const std::vector<int>& vector_characters,
    const MatterMultiplicities& matter_multiplicities, double timeout) {
    return expand_index(order, character_count, vector_characters, matter_multiplicities, 1, timeout);
}

std::vector<IndexFormTerm> expand_index(
    int order, int character_count, const std::vector<int>& vector_characters,
    const MatterMultiplicities& matter_multiplicities, int worker_count, double timeout) {
    ExpansionDeadline deadline(timeout);
    auto terms = expand_index_unordered_impl(order, character_count, vector_characters,
                                        matter_multiplicities, worker_count, deadline);
    DeadlinePoll poll(deadline);
    std::sort(terms.begin(), terms.end(), [&](const auto& a, const auto& b) {
        poll.poll();
        return term_less(a, b);
    });
    deadline.check();
    return terms;
}

std::vector<IndexFormTerm> expand_index_unordered(
    int order, int character_count, const std::vector<int>& vector_characters,
    const MatterMultiplicities& matter_multiplicities) {
    return expand_index_unordered(order, character_count, vector_characters,
                                  matter_multiplicities, 1, 600.0);
}

std::vector<IndexFormTerm> expand_index_unordered(
    int order, int character_count, const std::vector<int>& vector_characters,
    const MatterMultiplicities& matter_multiplicities, int worker_count) {
    return expand_index_unordered(order, character_count, vector_characters,
                                  matter_multiplicities, worker_count, 600.0);
}

std::vector<IndexFormTerm> expand_index_unordered(
    int order, int character_count, const std::vector<int>& vector_characters,
    const MatterMultiplicities& matter_multiplicities, double timeout) {
    return expand_index_unordered(order, character_count, vector_characters,
                                  matter_multiplicities, 1, timeout);
}

std::vector<IndexFormTerm> expand_index_unordered(
    int order, int character_count, const std::vector<int>& vector_characters,
    const MatterMultiplicities& matter_multiplicities, int worker_count, double timeout) {
    ExpansionDeadline deadline(timeout);
    return expand_index_unordered_impl(order, character_count, vector_characters,
                                       matter_multiplicities, worker_count, deadline);
}

}  // namespace n2scftdb
