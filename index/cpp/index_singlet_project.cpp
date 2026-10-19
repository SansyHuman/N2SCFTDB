#include "index_singlet_project.hpp"

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <utility>

namespace n2scftdb {
namespace {
using Structure = std::vector<std::pair<int, characters::Powers>>;

int factor_rank(const std::string& algebra) {
    long long rank;
    try { rank = std::stoll(algebra.substr(1)); }
    catch (const std::exception&) { throw std::invalid_argument("invalid gauge-factor rank"); }
    const char family = algebra.front();
    const bool valid = (family == 'A' && rank >= 1) ||
        ((family == 'B' || family == 'C') && rank >= 2) ||
        (family == 'D' && rank >= 3) || (family == 'E' && rank >= 6 && rank <= 8) ||
        (family == 'F' && rank == 4) || (family == 'G' && rank == 2);
    if (!valid || rank > std::numeric_limits<int>::max())
        throw std::invalid_argument("unsupported gauge-factor Cartan type");
    return static_cast<int>(rank);
}
} // namespace

ProjectedIndex project_index(
    const std::vector<IndexFormTerm>& terms,
    const std::vector<std::string>& gauge_factors,
    const std::vector<IndexCharacterSpec>& character_specs,
    characters::CharacterDecompositionCache& cache) {
    std::vector<std::string> algebras;
    std::vector<int> ranks;
    for (const auto& factor : gauge_factors) {
        algebras.push_back(characters::canonical_cartan_type(factor));
        ranks.push_back(factor_rank(algebras.back()));
    }
    for (const auto& spec : character_specs) {
        if (spec.factor < 0 || static_cast<std::size_t>(spec.factor) >= algebras.size())
            throw std::invalid_argument("character factor position is out of range");
        characters::canonical_labels(spec.labels);
        if (spec.labels.size() != static_cast<std::size_t>(ranks[spec.factor]))
            throw std::invalid_argument("Dynkin-label length does not match gauge-factor rank");
    }

    // Intern only the small character structures, never sort the expanded
    // outer vector. Every distinct structure is projected once for all terms.
    std::map<Structure, std::size_t> structures;
    std::vector<std::size_t> term_structures;
    term_structures.reserve(terms.size());
    for (const auto& term : terms) {
        if (term.t_power < 0) throw std::invalid_argument("t power must be nonnegative");
        Structure structure;
        structure.reserve(term.characters.size());
        for (const auto& part : term.characters) {
            if (part.character < 0 || static_cast<std::size_t>(part.character) >= character_specs.size())
                throw std::invalid_argument("formal character index is out of range");
            structure.emplace_back(part.character, characters::canonical_adams_powers(part.adams_powers));
        }
        std::sort(structure.begin(), structure.end());
        auto inserted = structures.emplace(std::move(structure), structures.size());
        term_structures.push_back(inserted.first->second);
    }
    if (terms.empty()) return {};

    std::vector<mpz_class> singlets(structures.size(), mpz_class(1));
    for (std::size_t factor = 0; factor < algebras.size(); ++factor) {
        // Canonicalize by labels as well as character ID: equivalent aliases
        // share one request and the existing Python-compatible SQLite key.
        std::map<characters::Product, std::size_t> distinct;
        std::vector<characters::Product> products;
        std::vector<std::size_t> product_ids(structures.size());
        for (const auto& [structure, id] : structures) {
            characters::Product product;
            for (const auto& [character, powers] : structure) {
                const auto& spec = character_specs[character];
                if (static_cast<std::size_t>(spec.factor) == factor)
                    product.emplace_back(spec.labels, powers);
            }
            product = characters::canonical_product(algebras[factor], product);
            auto inserted = distinct.emplace(product, products.size());
            if (inserted.second) products.push_back(std::move(product));
            product_ids[id] = inserted.first->second;
        }
        const auto values = cache.get_singlet_multiplicities(algebras[factor], ranks[factor], products);
        for (std::size_t id = 0; id < singlets.size(); ++id)
            singlets[id] *= values[product_ids[id]];
    }

    ProjectedIndex result;
    for (std::size_t i = 0; i < terms.size(); ++i) {
        if (singlets[term_structures[i]] == 0 || terms[i].coefficient == 0) continue;
        const auto& term = terms[i];
        const IndexPowers key{term.t_power, term.y_power, term.u_power};
        auto inserted = result.try_emplace(key, mpq_class(0));
        inserted.first->second += term.coefficient * singlets[term_structures[i]];
        if (inserted.first->second == 0) result.erase(inserted.first);
    }
    return result;
}

ProjectedIndex project_index(
    const std::vector<IndexFormTerm>& terms,
    const std::vector<std::string>& gauge_factors,
    const std::vector<IndexCharacterSpec>& character_specs,
    const std::string& cache_database_path) {
    characters::CharacterDecompositionCache cache(cache_database_path);
    return project_index(terms, gauge_factors, character_specs, cache);
}

} // namespace n2scftdb
