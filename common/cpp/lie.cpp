#include "lie.hpp"
#include "lie_bridge.h"

#include <algorithm>
#include <cctype>
#include <climits>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace n2scftdb::lie {
namespace {
static_assert(sizeof(unsigned short) == 2 && CHAR_BIT == 8,
              "LiE's binary coefficient interface requires 16-bit digits");
struct Algebra { char family; long rank; };
Algebra parse_algebra(const std::string& input) {
    std::string name;
    for (unsigned char c : input)
        if (!std::isspace(c) && c != '_' && c != '-') name += std::toupper(c);
    if (name.size() < 2 || !std::all_of(name.begin() + 1, name.end(),
        [](char c) { return c >= '0' && c <= '9'; }))
        throw std::invalid_argument("invalid LiE Cartan type");
    long rank;
    try { rank = std::stol(name.substr(1)); }
    catch (const std::exception&) { throw std::invalid_argument("invalid LiE Cartan rank"); }
    const char family = name.front();
    const bool valid = (family == 'A' && rank >= 1) ||
        ((family == 'B' || family == 'C') && rank >= 2) ||
        (family == 'D' && rank >= 3) || (family == 'E' && rank >= 6 && rank <= 8) ||
        (family == 'F' && rank == 4) || (family == 'G' && rank == 2);
    if (!valid || rank > INT_MAX) throw std::invalid_argument("unsupported LiE Cartan type");
    return {family, rank};
}
struct Input {
    std::vector<std::vector<long>> labels;
    std::vector<std::vector<unsigned short>> digits;
    std::vector<n2_lie_term> terms;

    Input(const Decomposition& character, long rank, long power, bool tensor) {
        labels.reserve(character.size()); digits.reserve(character.size()); terms.reserve(character.size());
        // LiE uses signed machine longs for weight arithmetic. Keep a
        // conservative margin for scalar products and Weyl transformations.
        const long double root_limit = std::sqrt(static_cast<long double>(LONG_MAX)) - 1;
        const auto weight_limit = static_cast<std::uint64_t>(root_limit /
            (32.0L * (rank + 1.0L) * (rank + 1.0L) * (tensor ? 2 : 1)));
        for (const auto& [weight, coefficient] : character) {
            if (weight.size() != static_cast<std::size_t>(rank))
                throw std::invalid_argument("Dynkin-label length does not match Cartan rank");
            std::uint64_t total = 0;
            for (auto value : weight) {
                if (value < 0 || static_cast<std::uint64_t>(value) > weight_limit - total)
                    throw std::invalid_argument("Dynkin labels exceed the native LiE weight range");
                total += static_cast<std::uint64_t>(value);
            }
            if (total && static_cast<std::uint64_t>(power) > weight_limit / total)
                throw std::invalid_argument("Adams power exceeds the native LiE weight range");
            if (coefficient == 0) continue;
            const auto count = (mpz_sizeinbase(coefficient.get_mpz_t(), 2) + 14) / 15;
            if (count > SHRT_MAX) throw std::overflow_error("coefficient exceeds LiE's bigint capacity");
            labels.emplace_back(weight.begin(), weight.end());
            digits.emplace_back(count);
            std::size_t written = 0;
            mpz_export(digits.back().data(), &written, -1, sizeof(unsigned short), 0, 1,
                       coefficient.get_mpz_t());
            terms.push_back({labels.back().data(), digits.back().data(), written,
                             mpz_sgn(coefficient.get_mpz_t())});
        }
    }
};
struct Result {
    n2_lie_result value{};
    ~Result() { n2_lie_result_free(&value); }
};
Decomposition calculate(const std::string& algebra, std::int64_t power,
                        const Decomposition& left, const Decomposition& right,
                        const Options& options) {
    const auto group = parse_algebra(algebra);
    if (options.max_objects <= 0 || options.max_objects > LONG_MAX)
        throw std::invalid_argument("native LiE max_objects must be a positive C long");
    if (!std::isfinite(options.timeout)) throw std::invalid_argument("native LiE timeout must be finite");
    if (power < 0 || power > LONG_MAX) throw std::invalid_argument("invalid native LiE Adams power");
    Input a(left, group.rank, power ? static_cast<long>(power) : 1, power == 0);
    Input b(right, group.rank, 1, power == 0);
    if (a.terms.empty() || (!power && b.terms.empty())) return {};
    Result result;
    if (n2_lie_compute(group.family, group.rank, static_cast<long>(power),
                       a.terms.data(), a.terms.size(), b.terms.data(), b.terms.size(),
                       static_cast<long>(options.max_objects), options.timeout, &result.value))
        throw std::runtime_error(result.value.error);
    Decomposition answer;
    for (std::size_t i = 0; i < result.value.count; ++i) {
        const auto& term = result.value.terms[i];
        if (!term.sign) continue;
        mpz_class coefficient;
        mpz_import(coefficient.get_mpz_t(), term.digit_count, -1, sizeof(unsigned short), 0, 1, term.digits);
        if (term.sign < 0) coefficient = -coefficient;
        Labels labels(term.labels, term.labels + group.rank);
        answer[labels] += coefficient;
        if (answer[labels] == 0) answer.erase(labels);
    }
    return answer;
}
} // namespace
Decomposition adams(const std::string& algebra, std::int64_t power,
                    const Decomposition& character, const Options& options) {
    if (power <= 0) throw std::invalid_argument("Adams power must be positive");
    return calculate(algebra, power, character, {}, options);
}
Decomposition adams(const std::string& algebra, std::int64_t power,
                    const Labels& labels, const Options& options) {
    return adams(algebra, power, Decomposition{{labels, 1}}, options);
}
Decomposition tensor(const std::string& algebra, const Decomposition& left,
                     const Decomposition& right, const Options& options) {
    return calculate(algebra, 0, left, right, options);
}
Decomposition tensor(const std::string& algebra, const Labels& left,
                     const Labels& right, const Options& options) {
    return tensor(algebra, Decomposition{{left, 1}}, Decomposition{{right, 1}}, options);
}
} // namespace n2scftdb::lie
