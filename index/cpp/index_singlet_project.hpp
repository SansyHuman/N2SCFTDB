#ifndef N2SCFTDB_INDEX_SINGLET_PROJECT_HPP
#define N2SCFTDB_INDEX_SINGLET_PROJECT_HPP

#include "index_expand.hpp"
#include "char_decomposition_cache.hpp"
#include <string>
#include <tuple>

namespace n2scftdb {

// Entry i describes formal character Ci. factor is a zero-based position in
// gauge_factors; labels are its Dynkin labels for that simple Cartan factor.
struct IndexCharacterSpec {
    int factor;
    characters::Labels labels;
};

using IndexPowers = std::tuple<int, std::int64_t, std::int64_t>; // t, y, u
using ProjectedIndex = std::map<IndexPowers, mpq_class>;

// Haar-project to the singlet of EVERY simple gauge factor. Input terms may
// have any order (including expand_index_unordered output) and may repeat.
// The result combines equal t/y/u powers, removes zeros and iterates in sorted
// exponent order. Exact rational coefficients and signed virtual Adams
// characters are retained; no full final-product decomposition is required.
//
// gauge_factors contains finite simple LiE Cartan names, e.g. {"A1", "A2"}.
// Every character spec and term is validated before cache access. Character
// indices and factor positions must be in range, labels nonnegative with the
// matching rank, t powers nonnegative, and Adams powers nonnegative with at
// least one positive entry. Inner character order and trailing padding need
// not be canonical. Zero gauge factors are allowed only with no characters.
// Invalid metadata throws std::invalid_argument; Adams overflow, cache and LiE
// errors propagate. Input is never modified. A failed call returns no partial
// index, but successful cache entries calculated before failure remain saved.
//
// This overload borrows a cache without closing it or changing its backend.
// It belongs to the calling thread, as required by CharacterDecompositionCache.
ProjectedIndex project_index(
    const std::vector<IndexFormTerm>& terms,
    const std::vector<std::string>& gauge_factors,
    const std::vector<IndexCharacterSpec>& character_specs,
    characters::CharacterDecompositionCache& cache);

// Convenience overload: uses SQLite caching and native in-process LiE by
// default. The default database path is relative to the working directory;
// pass the existing project's absolute cache path when running elsewhere.
// Cache/LiE settings (including per-calculation timeout) can be customized by
// constructing a cache and using the overload above. No Python is involved.
ProjectedIndex project_index(
    const std::vector<IndexFormTerm>& terms,
    const std::vector<std::string>& gauge_factors,
    const std::vector<IndexCharacterSpec>& character_specs,
    const std::string& cache_database_path = "char_decomposition_cache.db");

} // namespace n2scftdb
#endif
