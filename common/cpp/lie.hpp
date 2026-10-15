#ifndef N2SCFTDB_LIE_HPP
#define N2SCFTDB_LIE_HPP

#include <gmpxx.h>
#include <cstdint>
#include <map>
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

// Return independently owned exact values. LiE's process-global runtime is
// serialized internally. No LiE pointers, command strings or parsed output
// cross this interface. Only finite simple Cartan types are supported.
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
