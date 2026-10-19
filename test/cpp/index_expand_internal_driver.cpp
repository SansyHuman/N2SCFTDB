// A separate translation unit must be able to link the public unordered API.
#include "index/cpp/index_expand.hpp"
#include <algorithm>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <tuple>

namespace {
using Characters = std::vector<std::pair<int, std::vector<std::int64_t>>>;
using Key = std::tuple<int, std::int64_t, std::int64_t, Characters>;
Key key(const n2scftdb::IndexFormTerm& term) {
    Characters characters;
    for (const auto& part : term.characters)
        characters.emplace_back(part.character, part.adams_powers);
    return {term.t_power, term.y_power, term.u_power, characters};
}
std::map<Key, mpq_class> term_map(const std::vector<n2scftdb::IndexFormTerm>& terms) {
    std::map<Key, mpq_class> result;
    for (const auto& term : terms) {
        if (!result.emplace(key(term), term.coefficient).second)
            throw std::runtime_error("duplicate unordered output term");
    }
    return result;
}
}

int main() {
    try {
        struct Input {
            int order, character_count;
            std::vector<int> vectors;
            n2scftdb::MatterMultiplicities matter;
        };
        std::vector<Input> inputs{
            {0, 0, {}, {}}, {10, 0, {}, {{{}, -3}}},
            {16, 6, {0, 1, 2}, {{{3, 4, 5}, 1}}},
            {10, 3, {0, 1, 0}, {{{2, 2}, -2}, {{}, mpz_class(1) << 100}}},
            {2, 4201, {}, {}},
        };
        for (int i = 0; i < inputs.back().character_count; ++i)
            inputs.back().matter.emplace(n2scftdb::IndexedMonomial{i, i}, i % 2 ? -i : i + 1);
        bool saw_unordered = false;
        for (const auto& input : inputs) {
            const auto reference = n2scftdb::expand_index(input.order, input.character_count,
                                                         input.vectors, input.matter, -1.0);
            for (int workers : {1, 4}) {
                const auto raw = n2scftdb::expand_index_unordered(input.order, input.character_count,
                                                                 input.vectors, input.matter, workers, 10.0);
                if (term_map(raw) != term_map(reference))
                    throw std::runtime_error("unsorted terms differ from the sorted result");
                auto compare = [](const auto& a, const auto& b) { return key(a) < key(b); };
                saw_unordered |= !std::is_sorted(raw.begin(), raw.end(), compare);
                if (!std::is_sorted(reference.begin(), reference.end(), compare))
                    throw std::runtime_error("public output is no longer sorted");
            }
        }
        if (!saw_unordered) throw std::runtime_error("unordered entry point unexpectedly sorts every output");
        const auto reference = term_map(n2scftdb::expand_index(8, 1, {0}, {{{0}, 4}}));
        if (term_map(n2scftdb::expand_index_unordered(8, 1, {0}, {{{0}, 4}})) != reference ||
            term_map(n2scftdb::expand_index_unordered(8, 1, {0}, {{{0}, 4}}, 4)) != reference ||
            term_map(n2scftdb::expand_index_unordered(8, 1, {0}, {{{0}, 4}}, -1.0)) != reference)
            throw std::runtime_error("unordered overloads differ");
        for (int workers : {1, 4}) {
            for (double timeout : {0.0, 0.01}) {
                try {
                    n2scftdb::expand_index_unordered(30, 6, {0, 1, 2}, {{{3, 4, 5}, 1}}, workers, timeout);
                    throw std::logic_error("unordered timeout did not expire");
                } catch (const std::runtime_error& error) {
                    if (std::string(error.what()) != "index expansion timed out") throw;
                }
            }
        }
        try { n2scftdb::expand_index_unordered(0, 0, {}, {}, 0); return 1; }
        catch (const std::invalid_argument&) {}
        try { n2scftdb::expand_index_unordered(0, 0, {}, {}, std::numeric_limits<double>::infinity()); return 1; }
        catch (const std::invalid_argument&) {}
        std::cout << "public unordered-output checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
