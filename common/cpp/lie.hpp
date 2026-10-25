#ifndef N2SCFTDB_LIE_HPP
#define N2SCFTDB_LIE_HPP

#include <gmpxx.h>
#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace n2scftdb::lie {
using Labels = std::vector<std::int64_t>;
using Decomposition = std::map<Labels, mpz_class>;

struct Options {
    std::int64_t max_objects = 9999999;
    // A cooperative deadline, sampled during allocation and Weyl enumeration
    // and checked unconditionally at calculation boundaries.
    // Negative means unlimited. No signals or helper processes are used.
    double timeout = 600.0;
};

// A private LiE library instance, loaded once and reused for successive calls.
// Distinct environments can calculate concurrently with independent globals,
// deadlines and recovery state. Linux memfd + dlopen is required; no files are
// retained on disk. Destruction must not overlap a call on this environment.
// Returned exact values own their storage and outlive the environment.
class Environment {
public:
    Environment();
    ~Environment();
    Environment(const Environment&) = delete;
    Environment& operator=(const Environment&) = delete;
    Decomposition adams(const std::string& algebra, std::int64_t power,
                        const Decomposition& character, const Options& options = {});
    Decomposition adams(const std::string& algebra, std::int64_t power,
                        const Labels& labels, const Options& options = {});
    Decomposition tensor(const std::string& algebra, const Decomposition& left,
                         const Decomposition& right, const Options& options = {});
    Decomposition tensor(const std::string& algebra, const Labels& left,
                         const Labels& right, const Options& options = {});
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

// Compatibility functions use the original, serialized library instance.
// Use separate Environments for parallel calls. No LiE pointers, command strings
// or parsed output cross either interface. Only finite simple Cartan types
// are supported.
Decomposition adams(const std::string& algebra, std::int64_t power,
                    const Decomposition& character, const Options& options = {});
Decomposition adams(const std::string& algebra, std::int64_t power,
                    const Labels& labels, const Options& options = {});
Decomposition tensor(const std::string& algebra, const Decomposition& left,
                     const Decomposition& right, const Options& options = {});
Decomposition tensor(const std::string& algebra, const Labels& left,
                     const Labels& right, const Options& options = {});
} // namespace n2scftdb::lie
#endif
