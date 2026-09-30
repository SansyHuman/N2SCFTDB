// Test-only executable. This is deliberately not a Python extension or an
// application backend; its text protocol lets the regression suite inspect
// every exact formal coefficient returned by the independent C++ function.
#include "index/cpp/index_expand.hpp"

#include <chrono>
#include <exception>
#include <future>
#include <iomanip>
#include <iostream>
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

void expand_from_stdin(int worker_count, bool explicit_workers) {
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
    const auto terms = explicit_workers
        ? n2scftdb::expand_index(order, character_count, vectors, matter, worker_count)
        : n2scftdb::expand_index(order, character_count, vectors, matter);
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
        } else if (argc == 1) {
            expand_from_stdin(1, false);
        } else if (argc == 3 && std::string(argv[1]) == "--workers") {
            std::size_t parsed = 0;
            const std::string argument(argv[2]);
            const int worker_count = std::stoi(argument, &parsed);
            if (parsed != argument.size()) {
                throw std::invalid_argument("invalid worker count");
            }
            expand_from_stdin(worker_count, true);
        } else {
            throw std::invalid_argument(
                "usage: index_expand_driver [--workers N | --self-test | --parallel-self-test]");
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
