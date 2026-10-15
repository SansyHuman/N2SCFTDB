// Test-only translation unit: include the implementation to exercise its
// anonymous-namespace entry point without exporting an internal API.
#include "index/cpp/index_expand.cpp"
#include <iostream>

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
            throw std::runtime_error("duplicate internal output term");
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
                n2scftdb::ExpansionDeadline deadline(10.0);
                const auto raw = n2scftdb::expand_index_unordered(input.order, input.character_count,
                                                                 input.vectors, input.matter, workers, deadline);
                if (term_map(raw) != term_map(reference))
                    throw std::runtime_error("internal unsorted terms differ from the public result");
                auto compare = [](const auto& a, const auto& b) { return key(a) < key(b); };
                saw_unordered |= !std::is_sorted(raw.begin(), raw.end(), compare);
                if (!std::is_sorted(reference.begin(), reference.end(), compare))
                    throw std::runtime_error("public output is no longer sorted");
            }
        }
        if (!saw_unordered) throw std::runtime_error("internal entry point unexpectedly sorts every output");
        std::cout << "internal unordered-output checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
