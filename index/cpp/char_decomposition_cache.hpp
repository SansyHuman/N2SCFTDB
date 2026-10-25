#ifndef N2SCFTDB_CHAR_DECOMPOSITION_CACHE_HPP
#define N2SCFTDB_CHAR_DECOMPOSITION_CACHE_HPP

#include <gmpxx.h>
#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include "../../common/cpp/lie.hpp"

namespace n2scftdb::characters {
using Labels = lie::Labels;
using Powers = std::vector<std::int64_t>;
using Decomposition = lie::Decomposition;
using Product = std::vector<std::pair<Labels, Powers>>;
struct Request {
    std::string algebra;
    Labels labels;
    Powers powers;
    bool operator<(const Request& other) const;
    bool operator==(const Request& other) const;
};
struct ProcessResult {
    int returncode = 0;
    std::string stdout_text;
    std::string stderr_text;
};
class SqliteError : public std::runtime_error {
public:
    SqliteError(int code, const std::string& message);
    int code() const noexcept { return code_; }
private:
    int code_;
};

std::string canonical_cartan_type(const std::string& value);
Labels canonical_labels(const Labels& labels);
Powers canonical_adams_powers(const Powers& powers);
std::int64_t adams_order(const Powers& powers);
Request canonical_request(const std::string& algebra, const Labels& labels, const Powers& powers);
Product canonical_product(const std::string& algebra, const Product& product);
std::vector<Powers> decomposition_dependencies(const Powers& powers);
std::vector<int> dual_permutation(const std::string& algebra);
mpz_class singlet_pair(const std::string& algebra, const Decomposition& left, const Decomposition& right);
std::pair<Product, Product> split_character_product(const std::string& algebra, const Product& product);
std::string format_labels(const Labels& labels);
std::string format_lie_decomposition(const Decomposition& decomposition);
Decomposition parse_lie_decomposition(const std::string& text, int rank);
std::string encode_decomposition(const Decomposition& decomposition);
Decomposition decode_decomposition(const std::string& payload);
std::string character_product_key(const Product& product);
std::string decomposed_product_key(const std::vector<Decomposition>& product);
std::vector<std::string> lie_output_lines(const std::string& stdout_text);

// A cache instance owns one lazy SQLite connection, its memory caches, and a
// lazy isolated native LiE environment. Cache hits do not load an environment.
// Use a separate instance per thread; distinct instances may share one database.
// close() releases all three; the instance can subsequently be reused.
// After fork it discards inherited state before the next cache/runtime access.
// auto uses in-process LiE for the default executable, and subprocess mode for
// a custom executable. Native deadlines are cooperative. Coefficients are exact.
class CharacterDecompositionCache {
public:
    explicit CharacterDecompositionCache(std::string database_path,
        std::string lie_executable = "lie", std::int64_t max_nodes = 9999999,
        std::int64_t max_objects = 9999999, double timeout = 600.0,
        std::string lie_backend = "auto");
    ~CharacterDecompositionCache();
    CharacterDecompositionCache(const CharacterDecompositionCache&) = delete;
    CharacterDecompositionCache& operator=(const CharacterDecompositionCache&) = delete;
    void close();
    std::string lie_backend() const;

    std::optional<Decomposition> lookup_decomposition(const Request& request);
    void write_decompositions(const std::vector<std::pair<Request, Decomposition>>& entries);
    Decomposition get_decomposition(const std::string& algebra, const Labels& labels, const Powers& powers);
    std::vector<Decomposition> get_decompositions(const std::vector<Request>& requests);
    std::vector<Powers> decomposition_dependencies(const std::string& algebra,
        const Labels& labels, const Powers& powers);
    Decomposition calculate_decomposition(const std::string& algebra, const Labels& labels, const Powers& powers);
    Decomposition tensor_decompositions(const std::string& algebra,
        const Decomposition& left, const Decomposition& right);

    std::map<std::string, mpz_class> lookup_singlets(const std::string& algebra,
        const std::vector<std::string>& keys);
    void write_singlets(const std::string& algebra, const std::map<std::string, mpz_class>& entries);
    std::vector<mpz_class> get_singlet_multiplicities(const std::string& algebra,
        int rank, const std::vector<Product>& products);
    std::vector<mpz_class> singlet_multiplicities(const std::string& algebra,
        int rank, const std::vector<std::vector<Decomposition>>& products);
    std::vector<mpz_class> character_singlets(const std::string& algebra,
        int rank, const std::vector<Product>& products);
    std::vector<mpz_class> compute_singlet_multiplicities(const std::string& algebra,
        int rank, const std::vector<std::vector<Decomposition>>& products);

    ProcessResult execute_lie(const std::vector<std::string>& expressions, bool configure_memory);
    std::vector<std::string> run_lie(const std::vector<std::string>& expressions);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace n2scftdb::characters
#endif
