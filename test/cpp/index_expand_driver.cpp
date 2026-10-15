// Test-only executable. This is deliberately not a Python extension or an
// application backend; its text protocol lets the regression suite inspect
// every exact formal coefficient returned by the independent C++ function.
#include "index/cpp/index_expand.hpp"

#include <chrono>
#include <exception>
#include <future>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>

namespace {

template <typename Function>
void expect_invalid_argument(Function function, const char* label) {
    try {
        function();
    } catch (const std::invalid_argument&) {
        return;
    }
    throw std::runtime_error(std::string("did not reject ") + label);
}

void test_invalid_inputs() {
    using n2scftdb::expand_index;
    expect_invalid_argument([] { expand_index(-1, 0, {}, {}); }, "negative cutoff");
    expect_invalid_argument([] { expand_index(2, -1, {}, {}); }, "negative character count");
    expect_invalid_argument([] { expand_index(2, 1, {-1}, {}); }, "negative vector index");
    expect_invalid_argument([] { expand_index(2, 1, {1}, {}); }, "undeclared vector index");
    expect_invalid_argument([] { expand_index(2, 0, {0}, {}); }, "vector without characters");
    expect_invalid_argument([] { expand_index(2, 1, {}, {{{-1}, 1}}); }, "negative matter index");
    expect_invalid_argument([] { expand_index(2, 1, {}, {{{1}, 1}}); }, "undeclared matter index");
    expect_invalid_argument([] { expand_index(0, 1, {1}, {}); }, "invalid vector at vacuum cutoff");
    expect_invalid_argument([] { expand_index(0, 1, {}, {{{1}, 0}}); }, "invalid zero matter at vacuum cutoff");
    for (int workers : {0, -1}) {
        expect_invalid_argument([workers] { expand_index(4, 1, {0}, {}, workers); },
                                "nonpositive worker count");
        expect_invalid_argument([workers] { expand_index(0, 0, {}, {}, workers); },
                                "nonpositive worker count at vacuum cutoff");
    }
    for (double timeout : {std::numeric_limits<double>::infinity(),
                           -std::numeric_limits<double>::infinity(),
                           std::numeric_limits<double>::quiet_NaN()}) {
        expect_invalid_argument([timeout] { expand_index(4, 1, {0}, {}, timeout); },
                                "nonfinite serial timeout");
        expect_invalid_argument([timeout] { expand_index(4, 1, {0}, {}, 2, timeout); },
                                "nonfinite parallel timeout");
    }
    std::cout << "invalid-input checks passed\n";
}

// Compare every field in the returned order, not just the number of terms or
// summed coefficients. This also catches nondeterministic ordering and Adams
// tuple padding differences between independent concurrent calls.
void expect_same_terms(const std::vector<n2scftdb::IndexFormTerm>& actual,
                       const std::vector<n2scftdb::IndexFormTerm>& expected) {
    if (actual.size() != expected.size()) {
        throw std::runtime_error("parallel expansion term count differs from serial");
    }
    for (std::size_t i = 0; i < actual.size(); ++i) {
        const auto& a = actual[i];
        const auto& b = expected[i];
        if (a.coefficient != b.coefficient || a.t_power != b.t_power
            || a.y_power != b.y_power || a.u_power != b.u_power
            || a.characters.size() != b.characters.size()) {
            throw std::runtime_error("parallel expansion term differs from serial");
        }
        for (std::size_t j = 0; j < a.characters.size(); ++j) {
            if (a.characters[j].character != b.characters[j].character
                || a.characters[j].adams_powers != b.characters[j].adams_powers) {
                throw std::runtime_error("parallel character tuple differs from serial");
            }
        }
    }
}

void test_parallel_calls() {
    struct Input {
        int order;
        int characters;
        std::vector<int> vectors;
        n2scftdb::MatterMultiplicities matter;
    };
    const mpz_class large("1267650600228229401496703205499");
    const std::vector<Input> inputs{
        {18, 6, {0, 1, 2}, {{{3, 4, 5}, 1}}},
        {16, 3, {0, 1, 0}, {{{2, 2}, -2}, {{}, 3}}},
        {10, 2, {0}, {{{1}, large}, {{}, -large}}},
        {18, 2, {}, {{{0, 1}, large}, {{1, 0}, -large}}},
        {1, 0, {}, {}},
    };
    std::vector<std::vector<n2scftdb::IndexFormTerm>> expected;
    for (const auto& input : inputs) {
        expected.push_back(n2scftdb::expand_index(
            input.order, input.characters, input.vectors, input.matter));
    }
    // Separate callers share immutable input data. Each call owns its worker
    // threads and accumulation state, so there must be no global call state.
    std::vector<std::future<void>> calls;
    for (int caller = 0; caller < 4; ++caller) {
        calls.push_back(std::async(std::launch::async, [&, caller] {
            for (int repeat = 0; repeat < 2; ++repeat) {
                const int workers = (caller + repeat) % 2 == 0 ? 2 : 4;
                for (std::size_t i = 0; i < inputs.size(); ++i) {
                    const auto& input = inputs[i];
                    expect_same_terms(n2scftdb::expand_index(
                        input.order, input.characters, input.vectors, input.matter,
                        workers), expected[i]);
                }
            }
        }));
    }
    for (auto& call : calls) {
        call.get();
    }
    // More requested workers than useful tasks, including an early return.
    for (const auto& input : std::vector<Input>{{4, 1, {0}, {{{0}, 2}}},
                                               {0, 0, {}, {}}}) {
        expect_same_terms(n2scftdb::expand_index(
            input.order, input.characters, input.vectors, input.matter, 32),
            n2scftdb::expand_index(input.order, input.characters,
                                  input.vectors, input.matter));
    }
    std::cout << "parallel-call checks passed\n";
}

void test_output_construction() {
    // At these cutoffs only the leading hyper letter contributes, so the exact
    // answer is vacuum + sum_i m_i*t^2/u*C_i(1)^2. The 4201 recurrence
    // products stay below its parallel threshold, while the returned vector is
    // large enough for parallel output construction. One degree owns almost
    // every term, and degrees 1 and (at cutoff 3) 3 are empty.
    constexpr int character_count = 4201;
    const mpz_class large("1267650600228229401496703205499");
    n2scftdb::MatterMultiplicities matter;
    for (int character = 0; character < character_count; ++character) {
        mpz_class multiplicity = large + character;
        if (character % 2 != 0) {
            multiplicity = -multiplicity;
        }
        matter.emplace(n2scftdb::IndexedMonomial{character, character},
                       std::move(multiplicity));
    }
    const auto expected = n2scftdb::expand_index(2, character_count, {}, matter);
    if (expected.size() != static_cast<std::size_t>(character_count) + 1) {
        throw std::runtime_error("output construction lost or added a term");
    }
    expect_same_terms({expected.front()}, {{mpq_class(1), 0, 0, 0, {}}});
    for (int character = 0; character < character_count; ++character) {
        const auto& term = expected[static_cast<std::size_t>(character) + 1];
        const auto& multiplicity = matter.at({character, character});
        if (term.coefficient != mpq_class(multiplicity) || term.t_power != 2
            || term.y_power != 0 || term.u_power != -1
            || term.characters.size() != 1
            || term.characters.front().character != character
            || term.characters.front().adams_powers
                   != std::vector<std::int64_t>{2, 0}) {
            throw std::runtime_error("leading hyper output does not match its exact formula");
        }
    }
    for (int order : {2, 3}) {
        for (int workers : {1, 2, 4, 32}) {
            expect_same_terms(n2scftdb::expand_index(
                order, character_count, {}, matter, workers), expected);
        }
    }
    // Repeated independent callers create output workers without a preceding
    // parallel recurrence, then destroy them before returning.
    std::vector<std::future<void>> calls;
    for (int caller = 0; caller < 4; ++caller) {
        calls.push_back(std::async(std::launch::async, [&, caller] {
            for (int repeat = 0; repeat < 2; ++repeat) {
                expect_same_terms(n2scftdb::expand_index(
                    3, character_count, {}, matter, caller % 2 == 0 ? 2 : 4),
                    expected);
            }
        }));
    }
    for (auto& call : calls) {
        call.get();
    }
    std::cout << "output-construction checks passed\n";
}

template <typename Function>
void expect_timeout(Function function) {
    const auto start = std::chrono::steady_clock::now();
    try {
        function();
    } catch (const std::runtime_error& error) {
        if (std::string(error.what()) != "index expansion timed out") throw;
        if (std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count() > 2.0) {
            throw std::runtime_error("index expansion did not stop promptly after timeout");
        }
        return;
    }
    throw std::runtime_error("index expansion did not enforce timeout");
}

void test_timeouts() {
    using n2scftdb::expand_index;
    for (int order : {0, 1, 8}) {
        expect_timeout([order] { expand_index(order, 1, {0}, {}, 0.0); });
        expect_timeout([order] { expand_index(order, 1, {0}, {}, 4, 0.0); });
    }
    const n2scftdb::MatterMultiplicities matter{{{3, 4, 5}, 1}};
    for (int workers : {1, 4}) {
        for (double timeout : {0.01, 0.1}) {
            expect_timeout([&] { expand_index(30, 6, {0, 1, 2}, matter, workers, timeout); });
            expect_same_terms(expand_index(10, 6, {0, 1, 2}, matter, workers, 10.0),
                              expand_index(10, 6, {0, 1, 2}, matter, -1.0));
        }
    }
    // An expiring call must not cancel an independent caller or leave workers
    // alive after unwinding. Exercise success and expiration simultaneously.
    const auto expected = expand_index(16, 6, {0, 1, 2}, matter, -1.0);
    auto cancelled = std::async(std::launch::async, [&] {
        expect_timeout([&] { expand_index(30, 6, {0, 1, 2}, matter, 4, 0.05); });
    });
    auto completed = std::async(std::launch::async, [&] {
        expect_same_terms(expand_index(16, 6, {0, 1, 2}, matter, 2, -1.0), expected);
    });
    cancelled.get();
    completed.get();
    expect_same_terms(expand_index(10, 6, {0, 1, 2}, matter, 10.0),
                      expand_index(10, 6, {0, 1, 2}, matter));
    std::cout << "timeout checks passed\n";
}

template <typename Integer>
Integer read_integer() {
    Integer result;
    if (!(std::cin >> result)) {
        throw std::invalid_argument("missing or invalid integer in test input");
    }
    return result;
}

int read_count() {
    const int count = read_integer<int>();
    if (count < 0) {
        throw std::invalid_argument("negative collection length in test input");
    }
    return count;
}

void expand_from_stdin(int worker_count, bool explicit_workers,
                       double timeout = 600.0, bool explicit_timeout = false) {
    const int order = read_integer<int>();
    const int character_count = read_integer<int>();
    const int vector_count = read_count();
    std::vector<int> vectors;
    for (int i = 0; i < vector_count; ++i) {
        vectors.push_back(read_integer<int>());
    }
    const int matter_count = read_count();
    n2scftdb::MatterMultiplicities matter;
    for (int i = 0; i < matter_count; ++i) {
        std::string decimal;
        if (!(std::cin >> decimal)) {
            throw std::invalid_argument("missing matter multiplicity");
        }
        const mpz_class multiplicity(decimal, 10);
        const int length = read_count();
        n2scftdb::IndexedMonomial monomial;
        for (int j = 0; j < length; ++j) {
            monomial.push_back(read_integer<int>());
        }
        if (!matter.emplace(std::move(monomial), multiplicity).second) {
            throw std::invalid_argument("duplicate matter key in test input");
        }
    }
    std::string trailing;
    if (std::cin >> trailing) {
        throw std::invalid_argument("trailing test input");
    }

    const auto start = std::chrono::steady_clock::now();
    const auto terms = explicit_timeout
        ? (explicit_workers
            ? n2scftdb::expand_index(order, character_count, vectors, matter, worker_count, timeout)
            : n2scftdb::expand_index(order, character_count, vectors, matter, timeout))
        : (explicit_workers
            ? n2scftdb::expand_index(order, character_count, vectors, matter, worker_count)
            : n2scftdb::expand_index(order, character_count, vectors, matter));
    const auto finish = std::chrono::steady_clock::now();
    std::cerr << std::setprecision(17) << "{\"expansion_seconds\":"
              << std::chrono::duration<double>(finish - start).count()
              << ",\"term_count\":" << terms.size()
              << ",\"worker_count\":" << worker_count << "}\n";
    for (const auto& term : terms) {
        std::cout << term.t_power << ' ' << term.y_power << ' ' << term.u_power
                  << ' ' << term.coefficient.get_num() << ' ' << term.coefficient.get_den()
                  << ' ' << term.characters.size();
        for (const auto& character : term.characters) {
            std::cout << ' ' << character.character << ' ' << character.adams_powers.size();
            for (const auto power : character.adams_powers) {
                std::cout << ' ' << power;
            }
        }
        std::cout << '\n';
    }
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--self-test") {
            test_invalid_inputs();
        } else if (argc == 2 && std::string(argv[1]) == "--parallel-self-test") {
            test_parallel_calls();
        } else if (argc == 2 && std::string(argv[1]) == "--output-self-test") {
            test_output_construction();
        } else if (argc == 2 && std::string(argv[1]) == "--timeout-self-test") {
            test_timeouts();
        } else {
            int worker_count = 1;
            double timeout = 600.0;
            bool explicit_workers = false, explicit_timeout = false;
            for (int i = 1; i < argc; i += 2) {
                if (i + 1 >= argc) throw std::invalid_argument("missing option value");
                const std::string option(argv[i]), argument(argv[i + 1]);
                std::size_t parsed = 0;
                if (option == "--workers" && !explicit_workers) {
                    worker_count = std::stoi(argument, &parsed);
                    explicit_workers = true;
                } else if (option == "--timeout" && !explicit_timeout) {
                    timeout = std::stod(argument, &parsed);
                    explicit_timeout = true;
                } else {
                    throw std::invalid_argument("unknown or repeated option");
                }
                if (parsed != argument.size()) throw std::invalid_argument("invalid option value");
            }
            expand_from_stdin(worker_count, explicit_workers, timeout, explicit_timeout);
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
