#include "char_decomposition_cache.hpp"

#include <nlohmann/json.hpp>
#include <sqlite3.h>
#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cctype>
#include <climits>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <functional>
#include <limits>
#include <numeric>
#include <regex>
#include <set>
#include <sstream>
#include <thread>
#include <tuple>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

extern char** environ;
namespace n2scftdb::characters {
namespace {
using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;
std::int64_t checked_add(std::int64_t a, std::int64_t b) {
    if ((b > 0 && a > std::numeric_limits<std::int64_t>::max() - b) ||
        (b < 0 && a < std::numeric_limits<std::int64_t>::min() - b))
        throw std::overflow_error("character exponent overflow");
    return a + b;
}
bool trivial(const Labels& labels) {
    return std::all_of(labels.begin(), labels.end(), [](auto n) { return n == 0; });
}
std::int64_t factor_count(const Powers& powers) {
    std::int64_t result = 0;
    for (auto n : powers) {
        if (n < 0) throw std::invalid_argument("Adams powers must be nonnegative integers");
        result = checked_add(result, n);
    }
    return result;
}
int algebra_rank(const std::string& algebra) {
    try {
        auto rank = std::stoll(algebra.substr(1));
        if (rank > INT_MAX) throw std::out_of_range("rank");
        return static_cast<int>(rank);
    } catch (const std::exception&) {
        throw std::invalid_argument("Cartan rank exceeds supported integer range");
    }
}
void validate_rank(const std::string& algebra, int rank) {
    if (rank < 0 || rank != algebra_rank(algebra))
        throw std::invalid_argument("rank does not match Cartan type");
}
// Match subprocess.run(text=True)'s universal newline conversion.
std::string universal_newlines(const std::string& text) {
    std::string result;
    result.reserve(text.size());
    for (std::size_t i = 0; i < text.size(); ++i) {
        if (text[i] == '\r') {
            result += '\n';
            if (i + 1 < text.size() && text[i + 1] == '\n') ++i;
        } else result += text[i];
    }
    return result;
}
std::string trim(const std::string& text) {
    auto first = text.find_first_not_of(" \t\r\n\v\f");
    if (first == std::string::npos) return {};
    return text.substr(first, text.find_last_not_of(" \t\r\n\v\f") - first + 1);
}
mpz_class parse_integer(const std::string& text) {
    const auto normalized = trim(text);
    const auto digits = !normalized.empty() && (normalized.front() == '+' || normalized.front() == '-') ? 1u : 0u;
    if (digits == normalized.size() ||
        !std::all_of(normalized.begin() + digits, normalized.end(), [](char c) { return c >= '0' && c <= '9'; }))
        throw std::invalid_argument("invalid integer coefficient");
    mpz_class result;
    const auto start = normalized.front() == '+' ? 1u : 0u;
    if (result.set_str(normalized.substr(start), 10) != 0)
        throw std::invalid_argument("invalid integer coefficient");
    return result;
}
Json product_json(const Product& product) {
    Json result = Json::array();
    for (const auto& [labels, powers] : product) result.push_back(Json::array({labels, powers}));
    return result;
}
void check_sqlite(sqlite3* connection, int code) {
    if (code != SQLITE_OK && code != SQLITE_ROW && code != SQLITE_DONE)
        throw SqliteError(code, connection ? sqlite3_errmsg(connection) : sqlite3_errstr(code));
}
class Statement {
public:
    Statement(sqlite3* connection, const char* sql) : connection_(connection) {
        check_sqlite(connection_, sqlite3_prepare_v2(connection, sql, -1, &statement_, nullptr));
    }
    ~Statement() { sqlite3_finalize(statement_); }
    Statement(const Statement&) = delete;
    void text(int index, const std::string& value) {
        check_sqlite(connection_, sqlite3_bind_text64(statement_, index, value.data(), value.size(), SQLITE_TRANSIENT, SQLITE_UTF8));
    }
    void integer(int index, std::int64_t value) {
        check_sqlite(connection_, sqlite3_bind_int64(statement_, index, value));
    }
    bool row() { int code = sqlite3_step(statement_); check_sqlite(connection_, code); return code == SQLITE_ROW; }
    void run() { if (row()) throw std::runtime_error("unexpected SQLite result row"); }
    std::string column(int index) {
        auto value = sqlite3_column_text(statement_, index);
        if (!value) return {};
        return std::string(reinterpret_cast<const char*>(value), sqlite3_column_bytes(statement_, index));
    }
    std::int64_t integer_column(int index) { return sqlite3_column_int64(statement_, index); }
    void reset() {
        check_sqlite(connection_, sqlite3_reset(statement_));
        check_sqlite(connection_, sqlite3_clear_bindings(statement_));
    }
private:
    sqlite3* connection_;
    sqlite3_stmt* statement_ = nullptr;
};
void execute_sql(sqlite3* connection, const char* sql) {
    check_sqlite(connection, sqlite3_exec(connection, sql, nullptr, nullptr, nullptr));
}
template<class F> void retry_busy(F operation) {
    for (int attempt = 0;; ++attempt) {
        try { operation(); return; }
        catch (const SqliteError& error) {
            int code = error.code() & 0xff;
            if ((code != SQLITE_BUSY && code != SQLITE_LOCKED) || attempt == 2) throw;
            std::this_thread::sleep_for(std::chrono::milliseconds(50 * (attempt + 1)));
        }
    }
}
template<class F> void transaction(sqlite3* connection, F operation) {
    retry_busy([&] {
        execute_sql(connection, "BEGIN IMMEDIATE");
        try { operation(); execute_sql(connection, "COMMIT"); }
        catch (...) { sqlite3_exec(connection, "ROLLBACK", nullptr, nullptr, nullptr); throw; }
    });
}
class Fd {
public:
    int value = -1;
    Fd() = default;
    explicit Fd(int value) : value(value) {}
    ~Fd() { close(); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
    void close() { if (value >= 0) { ::close(value); value = -1; } }
};
void make_pipe(Fd& read_end, Fd& write_end) {
    int pair[2];
#if defined(__linux__)
    if (::pipe2(pair, O_CLOEXEC) != 0)
#else
    if (::pipe(pair) != 0)
#endif
        throw std::runtime_error(std::string("pipe failed: ") + std::strerror(errno));
    read_end.value = pair[0]; write_end.value = pair[1];
    for (Fd* end : {&read_end, &write_end}) {
        if (end->value <= STDERR_FILENO) {
            int replacement = ::fcntl(end->value, F_DUPFD_CLOEXEC, STDERR_FILENO + 1);
            if (replacement < 0) throw std::runtime_error(std::string("fcntl failed: ") + std::strerror(errno));
            end->close(); end->value = replacement;
        }
#if !defined(__linux__)
        if (::fcntl(end->value, F_SETFD, FD_CLOEXEC) < 0)
            throw std::runtime_error(std::string("fcntl failed: ") + std::strerror(errno));
#endif
    }
}
void nonblocking(int fd) {
    int flags = ::fcntl(fd, F_GETFL);
    if (flags < 0 || ::fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0)
        throw std::runtime_error(std::string("fcntl failed: ") + std::strerror(errno));
}
// Block SIGPIPE only in the calling thread while writing to the child's pipe.
// Consume a newly generated signal before restoring the caller's mask.
class BlockSigpipe {
public:
    BlockSigpipe() {
        sigemptyset(&set_); sigaddset(&set_, SIGPIPE);
        int error = pthread_sigmask(SIG_BLOCK, &set_, &old_);
        if (error) throw std::runtime_error(std::string("pthread_sigmask failed: ") + std::strerror(error));
        sigset_t pending; sigpending(&pending);
        was_pending_ = sigismember(&pending, SIGPIPE) == 1;
    }
    ~BlockSigpipe() {
        if (!was_pending_) {
            timespec zero{0, 0};
            while (sigtimedwait(&set_, nullptr, &zero) < 0 && errno == EINTR) {}
        }
        pthread_sigmask(SIG_SETMASK, &old_, nullptr);
    }
private:
    sigset_t set_{}, old_{};
    bool was_pending_ = false;
};
class Child {
public:
    pid_t pid = -1;
    pid_t group = -1;
    ~Child() {
        if (group > 0) ::kill(-group, SIGKILL);
        if (pid > 0) {
            ::kill(pid, SIGKILL);
            while (::waitpid(pid, nullptr, 0) < 0 && errno == EINTR) {}
        }
    }
};
ProcessResult run_process(const std::string& executable, const std::string& input, double timeout) {
    Fd input_read, input_write, output_read, output_write, error_read, error_write;
    make_pipe(input_read, input_write); make_pipe(output_read, output_write); make_pipe(error_read, error_write);
    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attributes;
    int error = posix_spawn_file_actions_init(&actions);
    if (error) throw std::runtime_error(std::strerror(error));
    struct ActionsGuard { posix_spawn_file_actions_t* p; ~ActionsGuard() { posix_spawn_file_actions_destroy(p); } } actions_guard{&actions};
    error = posix_spawnattr_init(&attributes);
    if (error) throw std::runtime_error(std::strerror(error));
    struct AttrGuard { posix_spawnattr_t* p; ~AttrGuard() { posix_spawnattr_destroy(p); } } attr_guard{&attributes};
    const auto checked = [](int code) { if (code) throw std::runtime_error(std::strerror(code)); };
    checked(posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETPGROUP));
    checked(posix_spawnattr_setpgroup(&attributes, 0));
    checked(posix_spawn_file_actions_adddup2(&actions, input_read.value, STDIN_FILENO));
    checked(posix_spawn_file_actions_adddup2(&actions, output_write.value, STDOUT_FILENO));
    checked(posix_spawn_file_actions_adddup2(&actions, error_write.value, STDERR_FILENO));
    for (int fd : {input_read.value, input_write.value, output_read.value, output_write.value, error_read.value, error_write.value})
        if (fd > STDERR_FILENO) checked(posix_spawn_file_actions_addclose(&actions, fd));
#if defined(__GLIBC__)
#if __GLIBC_PREREQ(2, 34)
    checked(posix_spawn_file_actions_addclosefrom_np(&actions, STDERR_FILENO + 1));
#endif
#endif
    std::array<char*, 2> argv{const_cast<char*>(executable.c_str()), nullptr};
    Child child;
    error = posix_spawnp(&child.pid, executable.c_str(), &actions, &attributes, argv.data(), environ);
    if (error) {
        child.pid = -1;
        if (error == ENOENT) throw std::runtime_error("LiE executable '" + executable + "' was not found");
        throw std::runtime_error("could not execute LiE '" + executable + "': " + std::strerror(error));
    }
    child.group = child.pid;
    input_read.close(); output_write.close(); error_write.close();
    nonblocking(input_write.value); nonblocking(output_read.value); nonblocking(error_read.value);
    BlockSigpipe block_sigpipe;
    ProcessResult result;
    std::size_t written = 0;
    bool exited = false;
    int status = 0;
    auto start = Clock::now();
    if (input.empty()) input_write.close();
    const auto drain = [](Fd& fd, std::string& destination) {
        char buffer[16384];
        // Bound each drain so active writers cannot starve stdin or the deadline.
        for (int chunk = 0; chunk < 16; ++chunk) {
            auto count = ::read(fd.value, buffer, sizeof(buffer));
            if (count > 0) destination.append(buffer, static_cast<std::size_t>(count));
            else if (count == 0) { fd.close(); return; }
            else if (errno == EINTR) continue;
            else if (errno == EAGAIN || errno == EWOULDBLOCK) return;
            else throw std::runtime_error(std::string("reading LiE output failed: ") + std::strerror(errno));
        }
    };
    while (!exited || output_read.value >= 0 || error_read.value >= 0) {
        if (timeout >= 0 && std::chrono::duration<double>(Clock::now() - start).count() >= timeout)
            throw std::runtime_error("LiE calculation timed out");
        int wait_ms = output_read.value < 0 && error_read.value < 0 ? 1 : 50;
        if (timeout >= 0) wait_ms = std::max(0, static_cast<int>(std::min(static_cast<double>(wait_ms),
            (timeout - std::chrono::duration<double>(Clock::now() - start).count()) * 1000.0)));
        pollfd fds[3] = {{input_write.value, POLLOUT, 0}, {output_read.value, POLLIN, 0}, {error_read.value, POLLIN, 0}};
        if (::poll(fds, 3, wait_ms) < 0 && errno != EINTR)
            throw std::runtime_error(std::string("poll failed: ") + std::strerror(errno));
        if (input_write.value >= 0 && fds[0].revents) {
            auto count = ::write(input_write.value, input.data() + written, input.size() - written);
            if (count > 0) { written += static_cast<std::size_t>(count); if (written == input.size()) input_write.close(); }
            else if (count < 0 && errno == EPIPE) input_write.close();
            else if (count < 0 && errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)
                throw std::runtime_error(std::string("writing LiE input failed: ") + std::strerror(errno));
        }
        if (output_read.value >= 0 && fds[1].revents) drain(output_read, result.stdout_text);
        if (error_read.value >= 0 && fds[2].revents) drain(error_read, result.stderr_text);
        if (!exited) {
            auto waited = ::waitpid(child.pid, &status, WNOHANG);
            if (waited == child.pid) { exited = true; child.pid = -1; input_write.close(); }
            else if (waited < 0 && errno != EINTR) throw std::runtime_error(std::string("waitpid failed: ") + std::strerror(errno));
        }
    }
    child.group = -1;
    result.returncode = WIFEXITED(status) ? WEXITSTATUS(status) : -WTERMSIG(status);
    result.stdout_text = universal_newlines(result.stdout_text);
    result.stderr_text = universal_newlines(result.stderr_text);
    return result;
}
} // namespace

bool Request::operator<(const Request& other) const {
    return std::tie(algebra, labels, powers) < std::tie(other.algebra, other.labels, other.powers);
}
bool Request::operator==(const Request& other) const {
    return std::tie(algebra, labels, powers) == std::tie(other.algebra, other.labels, other.powers);
}
SqliteError::SqliteError(int code, const std::string& message) : std::runtime_error(message), code_(code) {}
std::string canonical_cartan_type(const std::string& value) {
    std::string result;
    for (unsigned char c : value) if (!std::isspace(c) && c != '_' && c != '-') result.push_back(static_cast<char>(std::toupper(c)));
    static const std::regex pattern("(?:[ABCD][0-9]+|E[678]|F4|G2)");
    if (!std::regex_match(result, pattern)) throw std::invalid_argument("invalid LiE Cartan type '" + value + "'");
    return result;
}
Labels canonical_labels(const Labels& labels) {
    if (labels.empty() || std::any_of(labels.begin(), labels.end(), [](auto n) { return n < 0; }))
        throw std::invalid_argument("Dynkin labels must be a nonempty tuple of nonnegative integers");
    return labels;
}
std::int64_t adams_order(const Powers& powers) {
    std::int64_t order = 0;
    for (std::size_t i = 0; i < powers.size(); ++i) {
        auto n = powers[i];
        if (n < 0) throw std::invalid_argument("Adams powers must be nonnegative integers");
        if (n && i + 1 > static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max() / n))
            throw std::overflow_error("Adams order exceeds supported integer range");
        order = checked_add(order, n * static_cast<std::int64_t>(i + 1));
    }
    return order;
}
Powers canonical_adams_powers(const Powers& powers) {
    auto order = adams_order(powers);
    if (order <= 0) throw std::invalid_argument("at least one Adams power must be positive");
    Powers result = powers;
    result.resize(static_cast<std::size_t>(order), 0);
    return result;
}
Request canonical_request(const std::string& algebra, const Labels& labels, const Powers& powers) {
    Request result{canonical_cartan_type(algebra), canonical_labels(labels), canonical_adams_powers(powers)};
    if (result.labels.size() != static_cast<std::size_t>(algebra_rank(result.algebra)))
        throw std::invalid_argument("Dynkin-label length does not match Cartan rank");
    return result;
}
Product canonical_product(const std::string& algebra, const Product& product) {
    std::map<Labels, Powers, std::greater<Labels>> merged;
    for (const auto& [labels, powers] : product) {
        auto request = canonical_request(algebra, labels, powers);
        auto& previous = merged[request.labels];
        previous.resize(std::max(previous.size(), request.powers.size()), 0);
        for (std::size_t i = 0; i < request.powers.size(); ++i) previous[i] = checked_add(previous[i], request.powers[i]);
    }
    Product result;
    for (const auto& [labels, powers] : merged) result.emplace_back(labels, canonical_adams_powers(powers));
    return result;
}
std::vector<Powers> decomposition_dependencies(const Powers& powers) {
    if (factor_count(powers) <= 1) return {};
    auto first = std::find_if(powers.begin(), powers.end(), [](auto n) { return n != 0; });
    auto position = static_cast<std::size_t>(first - powers.begin());
    auto left = powers; --left[position];
    Powers right(position + 1, 0); right.back() = 1;
    return {canonical_adams_powers(left), right};
}
std::vector<int> dual_permutation(const std::string& value) {
    auto algebra = canonical_cartan_type(value);
    const int rank = algebra_rank(algebra);
    std::vector<int> result(static_cast<std::size_t>(rank));
    std::iota(result.begin(), result.end(), 0);
    if (algebra.front() == 'A') std::reverse(result.begin(), result.end());
    else if (algebra.front() == 'D' && rank >= 3 && rank % 2) std::swap(result[rank - 2], result[rank - 1]);
    // Bourbaki/Sage/LiE numbering: the vertical node is 2 (zero-based 1).
    else if (algebra == "E6") result = {5, 1, 4, 3, 2, 0};
    return result;
}
mpz_class singlet_pair(const std::string& algebra, const Decomposition& a, const Decomposition& b) {
    if (a.empty() || b.empty()) return 0;
    const auto& left = a.size() <= b.size() ? a : b;
    const auto& right = a.size() <= b.size() ? b : a;
    auto permutation = dual_permutation(algebra);
    mpz_class result = 0;
    for (const auto& [labels, coefficient] : left) {
        if (labels.size() != permutation.size()) throw std::invalid_argument("Dynkin-label length does not match Cartan rank");
        Labels dual(labels.size());
        for (std::size_t i = 0; i < dual.size(); ++i) dual[i] = labels[permutation[i]];
        auto found = right.find(dual);
        if (found != right.end()) result += coefficient * found->second;
    }
    return result;
}
std::pair<Product, Product> split_character_product(const std::string& algebra, const Product& product) {
    using Atom = std::tuple<mpz_class, Labels, std::size_t>;
    std::vector<Atom> atoms;
    for (const auto& [labels, powers] : product) {
        mpz_class weight = 0;
        for (auto n : labels) weight += mpz_class(std::to_string(n));
        if (weight < 1) weight = 1;
        for (std::size_t i = 0; i < powers.size(); ++i)
            for (std::int64_t e = 0; e < powers[i]; ++e) atoms.emplace_back(weight * (i + 1), labels, i + 1);
    }
    std::sort(atoms.begin(), atoms.end(), std::greater<Atom>());
    Product sides[2]; mpz_class weights[2] = {0, 0};
    for (const auto& [weight, labels, adams] : atoms) {
        bool has[2];
        for (int i = 0; i < 2; ++i) has[i] = std::any_of(sides[i].begin(), sides[i].end(), [&](const auto& p) { return p.first == labels; });
        int side = std::make_tuple(weights[1], !has[1], 1) < std::make_tuple(weights[0], !has[0], 0) ? 1 : 0;
        Powers powers(adams, 0); powers.back() = 1;
        sides[side].emplace_back(labels, std::move(powers)); weights[side] += weight;
    }
    return {canonical_product(algebra, sides[0]), canonical_product(algebra, sides[1])};
}
std::string format_labels(const Labels& labels) { return Json(labels).dump(); }
std::string format_lie_decomposition(const Decomposition& decomposition) {
    if (decomposition.empty()) throw std::invalid_argument("the zero virtual representation has no LiE polynomial here");
    std::string result;
    for (const auto& [labels, coefficient] : decomposition) if (coefficient != 0) {
        if (coefficient > 0 && !result.empty()) result += '+';
        result += coefficient.get_str() + "X" + format_labels(labels);
    }
    return result;
}
Decomposition parse_lie_decomposition(const std::string& text, int rank) {
    std::string compact;
    for (unsigned char c : text) if (!std::isspace(c)) compact.push_back(static_cast<char>(c));
    std::string normalized;
    for (std::size_t i = 0; i < compact.size();) {
        if (compact[i] == '+' || compact[i] == '-') {
            bool negative = false;
            while (i < compact.size() && (compact[i] == '+' || compact[i] == '-')) { negative ^= compact[i] == '-'; ++i; }
            normalized += negative ? '-' : '+';
        } else normalized += compact[i++];
    }
    if (normalized == "0") return {};
    Decomposition result;
    std::size_t position = 0;
    const auto fail = [&]() { throw std::runtime_error("could not parse LiE decomposition: " + text); };
    const auto digit = [](char c) { return c >= '0' && c <= '9'; };
    while (position < normalized.size()) {
        auto coefficient_begin = position;
        if (normalized[position] == '+' || normalized[position] == '-') ++position;
        auto digits_begin = position;
        while (position < normalized.size() && digit(normalized[position])) ++position;
        if (position == digits_begin) fail();
        auto coefficient = parse_integer(normalized.substr(coefficient_begin, position - coefficient_begin));
        if (position + 2 > normalized.size() || normalized.compare(position, 2, "X[") != 0) fail();
        position += 2;
        Labels labels;
        for (;;) {
            auto label_begin = position;
            while (position < normalized.size() && digit(normalized[position])) ++position;
            if (position == label_begin || position == normalized.size()) fail();
            try { labels.push_back(std::stoll(normalized.substr(label_begin, position - label_begin))); }
            catch (const std::exception&) { throw std::runtime_error("LiE Dynkin label exceeds supported integer range"); }
            if (normalized[position] == ']') { ++position; break; }
            if (normalized[position++] != ',') fail();
        }
        if (labels.size() != static_cast<std::size_t>(rank))
            throw std::runtime_error("LiE returned " + std::to_string(labels.size()) + " labels for a rank-" + std::to_string(rank) + " algebra");
        result[labels] += coefficient;
        if (result[labels] == 0) result.erase(labels);
    }
    if (position == 0) throw std::runtime_error("could not parse LiE decomposition: " + text);
    return result;
}
std::string encode_decomposition(const Decomposition& decomposition) {
    Json data = Json::array();
    for (const auto& [labels, coefficient] : decomposition)
        if (coefficient != 0) data.push_back(Json::array({labels, coefficient.get_str()}));
    return data.dump();
}
Decomposition decode_decomposition(const std::string& payload) {
    auto data = Json::parse(payload);
    if (!data.is_array()) throw std::invalid_argument("decomposition payload must be an array");
    Decomposition result;
    for (const auto& item : data) {
        if (!item.is_array() || item.size() != 2 || !item[0].is_array()) throw std::invalid_argument("invalid decomposition record");
        Labels labels;
        for (const auto& value : item[0]) {
            if (!value.is_number_integer() || (value.is_number_unsigned() && value.get<std::uint64_t>() > static_cast<std::uint64_t>(INT64_MAX)))
                throw std::invalid_argument("invalid Dynkin label in decomposition payload");
            labels.push_back(value.get<std::int64_t>());
        }
        const auto& coefficient = item[1];
        if (!coefficient.is_string() && !coefficient.is_number_integer()) throw std::invalid_argument("invalid coefficient in decomposition payload");
        result[labels] = parse_integer(coefficient.is_string() ? coefficient.get<std::string>() : coefficient.dump());
    }
    return result;
}
std::string character_product_key(const Product& product) { return Json::array({"characters", product_json(product)}).dump(); }
std::string decomposed_product_key(const std::vector<Decomposition>& product) {
    std::vector<std::string> factors;
    for (const auto& factor : product) factors.push_back(encode_decomposition(factor));
    std::sort(factors.begin(), factors.end());
    return Json::array({"decompositions", factors}).dump();
}
std::vector<std::string> lie_output_lines(const std::string& stdout_text) {
    std::vector<std::string> result;
    std::istringstream input(universal_newlines(stdout_text)); std::string line;
    while (std::getline(input, line)) {
        if (line.rfind("New tree space with maximum number of nodes:", 0) == 0) continue;
        line = trim(line);
        if (!line.empty()) result.push_back(std::move(line));
    }
    return result;
}

struct CharacterDecompositionCache::Impl {
    std::string database_path, lie_executable;
    std::int64_t max_nodes, max_objects;
    double timeout;
    bool native_lie = true;
    std::unique_ptr<lie::Environment> environment;
    sqlite3* connection = nullptr;
    pid_t pid = ::getpid();
    std::map<Request, Decomposition> decompositions;
    std::map<std::pair<std::string, std::string>, mpz_class> singlets;
    std::map<std::pair<std::string, Product>, Decomposition> partial_products;
    Impl(std::string path, std::string executable, std::int64_t nodes, std::int64_t objects, double seconds)
        : database_path(std::move(path)), lie_executable(std::move(executable)),
          max_nodes(nodes), max_objects(objects), timeout(seconds) {}
    void clear() {
        if (connection) { sqlite3_close_v2(connection); connection = nullptr; }
        environment.reset();
        decompositions.clear(); singlets.clear(); partial_products.clear(); pid = ::getpid();
    }
    void state() { if (pid != ::getpid()) clear(); }
    lie::Environment& runtime() {
        state();
        if (!environment) environment = std::make_unique<lie::Environment>();
        return *environment;
    }
    sqlite3* db() {
        state();
        if (connection) return connection;
        if (database_path != ":memory:") {
            auto parent = std::filesystem::path(database_path).parent_path();
            if (!parent.empty()) std::filesystem::create_directories(parent);
        }
        sqlite3* opened = nullptr;
        int code = sqlite3_open_v2(database_path.c_str(), &opened, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nullptr);
        try {
            check_sqlite(opened, code);
            check_sqlite(opened, sqlite3_busy_timeout(opened, 30000));
            std::string mode;
            { Statement query(opened, "PRAGMA journal_mode"); if (query.row()) mode = query.column(0); }
            if (mode != "wal") retry_busy([&] { execute_sql(opened, "PRAGMA journal_mode=WAL"); });
            std::int64_t version = 0;
            { Statement query(opened, "PRAGMA user_version"); if (query.row()) version = query.integer_column(0); }
            if (version != 0 && version != 1)
                throw std::runtime_error("unsupported character-cache schema version " + std::to_string(version));
            if (version == 0) transaction(opened, [&] {
                execute_sql(opened, "CREATE TABLE IF NOT EXISTS character_decompositions ("
                    "algebra TEXT NOT NULL, dynkin_labels TEXT NOT NULL, adams_powers TEXT NOT NULL, "
                    "adams_order INTEGER NOT NULL, terms_json TEXT NOT NULL, "
                    "PRIMARY KEY (algebra, dynkin_labels, adams_powers)) WITHOUT ROWID");
                execute_sql(opened, "CREATE TABLE IF NOT EXISTS singlet_coefficients ("
                    "algebra TEXT NOT NULL, product_key TEXT NOT NULL, coefficient TEXT NOT NULL, "
                    "PRIMARY KEY (algebra, product_key)) WITHOUT ROWID");
                execute_sql(opened, "PRAGMA user_version=1");
            });
        } catch (...) { if (opened) sqlite3_close_v2(opened); throw; }
        connection = opened;
        return connection;
    }
};
CharacterDecompositionCache::CharacterDecompositionCache(std::string database_path, std::string lie_executable,
    std::int64_t max_nodes, std::int64_t max_objects, double timeout, std::string lie_backend)
    : impl_(std::make_unique<Impl>(std::move(database_path), std::move(lie_executable), max_nodes, max_objects, timeout)) {
    if (impl_->database_path.find('\0') != std::string::npos || impl_->lie_executable.find('\0') != std::string::npos)
        throw std::invalid_argument("embedded null character in database path or LiE executable");
    if (!std::isfinite(timeout)) throw std::invalid_argument("timeout must be finite or negative for no deadline");
    if (lie_backend != "auto" && lie_backend != "native" && lie_backend != "subprocess")
        throw std::invalid_argument("lie_backend must be auto, native, or subprocess");
    impl_->native_lie = lie_backend == "native" || (lie_backend == "auto" && impl_->lie_executable == "lie");
}
std::string CharacterDecompositionCache::lie_backend() const { return impl_->native_lie ? "native" : "subprocess"; }
CharacterDecompositionCache::~CharacterDecompositionCache() { impl_->clear(); }
void CharacterDecompositionCache::close() { impl_->clear(); }
std::optional<Decomposition> CharacterDecompositionCache::lookup_decomposition(const Request& input) {
    const auto request = canonical_request(input.algebra, input.labels, input.powers);
    impl_->state();
    auto found = impl_->decompositions.find(request);
    if (found != impl_->decompositions.end()) return found->second;
    Statement query(impl_->db(), "SELECT terms_json FROM character_decompositions WHERE algebra=? AND dynkin_labels=? AND adams_powers=?");
    query.text(1, request.algebra); query.text(2, Json(request.labels).dump()); query.text(3, Json(request.powers).dump());
    if (!query.row()) return std::nullopt;
    auto value = decode_decomposition(query.column(0));
    impl_->decompositions[request] = value;
    return value;
}
void CharacterDecompositionCache::write_decompositions(const std::vector<std::pair<Request, Decomposition>>& entries) {
    if (entries.empty()) return;
    struct Row { Request request; std::string labels, powers, payload; std::int64_t order; };
    std::vector<Row> rows; rows.reserve(entries.size());
    for (const auto& [input, value] : entries) {
        auto request = canonical_request(input.algebra, input.labels, input.powers);
        rows.push_back({request, Json(request.labels).dump(), Json(request.powers).dump(), encode_decomposition(value), adams_order(request.powers)});
    }
    auto connection = impl_->db();
    transaction(connection, [&] {
        Statement statement(connection, "INSERT INTO character_decompositions VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (algebra, dynkin_labels, adams_powers) DO NOTHING");
        for (const auto& row : rows) {
            statement.text(1, row.request.algebra); statement.text(2, row.labels); statement.text(3, row.powers);
            statement.integer(4, row.order); statement.text(5, row.payload); statement.run(); statement.reset();
        }
    });
}
Decomposition CharacterDecompositionCache::get_decomposition(const std::string& algebra, const Labels& labels, const Powers& powers) {
    auto request = canonical_request(algebra, labels, powers);
    auto value = lookup_decomposition(request);
    if (value) return *value;
    auto result = calculate_decomposition(request.algebra, request.labels, request.powers);
    write_decompositions({{request, result}});
    impl_->decompositions[request] = result;
    return result;
}
std::vector<Powers> CharacterDecompositionCache::decomposition_dependencies(const std::string& input_algebra,
    const Labels& input_labels, const Powers& input_powers) {
    auto request = canonical_request(input_algebra, input_labels, input_powers);
    const auto& algebra = request.algebra; const auto& labels = request.labels; const auto& powers = request.powers;
    auto usual = characters::decomposition_dependencies(powers);
    if (trivial(labels)) return {};
    if (factor_count(powers) <= 2) return usual;
    auto lookup = [&](const Powers& part) -> std::optional<Decomposition> {
        if (part == Powers{1}) return Decomposition{{labels, 1}};
        return lookup_decomposition({algebra, labels, part});
    };
    if (std::all_of(usual.begin(), usual.end(), [&](const auto& part) { return lookup(part).has_value(); })) return usual;
    std::set<Powers> available;
    {
        Statement query(impl_->db(), "SELECT adams_powers FROM character_decompositions WHERE algebra=? AND dynkin_labels=? AND adams_order<?");
        query.text(1, algebra); query.text(2, Json(labels).dump()); query.integer(3, static_cast<std::int64_t>(powers.size()));
        while (query.row()) available.insert(Json::parse(query.column(0)).get<Powers>());
    }
    for (const auto& [key, value] : impl_->decompositions)
        if (key.algebra == algebra && key.labels == labels && key.powers.size() < powers.size()) available.insert(key.powers);
    available.insert({1});
    using Score = std::tuple<mpz_class, std::size_t, Powers, Powers>;
    std::optional<Score> best;
    Labels zero(labels.size(), 0);
    for (const auto& left : available) {
        if (left.size() > powers.size()) continue;
        auto remainder = powers;
        for (std::size_t i = 0; i < left.size(); ++i) remainder[i] -= left[i];
        if (std::any_of(remainder.begin(), remainder.end(), [](auto n) { return n < 0; }) || factor_count(remainder) == 0) continue;
        auto right = canonical_adams_powers(remainder);
        if (!available.count(right) || left > right) continue;
        auto a = lookup(left), b = lookup(right);
        if (!a || !b) continue;
        bool scalar = (a->size() == 1 && a->count(zero)) || (b->size() == 1 && b->count(zero));
        mpz_class cost = 0;
        if (!a->empty() && !b->empty() && !scalar) { cost = a->size(); cost *= b->size(); }
        Score score{cost, a->size() + b->size(), left, right};
        if (!best || score < *best) best = std::move(score);
    }
    return best ? std::vector<Powers>{std::get<2>(*best), std::get<3>(*best)} : usual;
}
std::vector<Decomposition> CharacterDecompositionCache::get_decompositions(const std::vector<Request>& inputs) {
    std::vector<Request> requests; requests.reserve(inputs.size());
    for (const auto& request : inputs) requests.push_back(canonical_request(request.algebra, request.labels, request.powers));
    std::set<Request> required;
    std::vector<Request> pending = requests;
    std::map<std::int64_t, std::set<Request>> levels;
    while (!pending.empty()) {
        auto request = std::move(pending.back()); pending.pop_back();
        if (!required.insert(request).second || lookup_decomposition(request)) continue;
        levels[factor_count(request.powers)].insert(request);
        for (const auto& part : decomposition_dependencies(request.algebra, request.labels, request.powers))
            pending.push_back({request.algebra, request.labels, part});
    }
    for (const auto& [level, entries] : levels)
        for (const auto& request : entries) get_decomposition(request.algebra, request.labels, request.powers);
    std::vector<Decomposition> result; result.reserve(requests.size());
    for (const auto& request : requests) result.push_back(get_decomposition(request.algebra, request.labels, request.powers));
    return result;
}
Decomposition CharacterDecompositionCache::calculate_decomposition(const std::string& algebra_input, const Labels& labels_input, const Powers& powers_input) {
    auto request = canonical_request(algebra_input, labels_input, powers_input);
    const auto& algebra = request.algebra; const auto& labels = request.labels; const auto& powers = request.powers;
    if (powers == Powers{1} || trivial(labels)) return {{labels, 1}};
    if (factor_count(powers) > 1) {
        auto dependencies = decomposition_dependencies(algebra, labels, powers);
        auto left = get_decomposition(algebra, labels, dependencies.at(0));
        auto right = get_decomposition(algebra, labels, dependencies.at(1));
        return tensor_decompositions(algebra, left, right);
    }
    auto position = std::find_if(powers.begin(), powers.end(), [](auto n) { return n != 0; }) - powers.begin() + 1;
    if (impl_->native_lie)
        return impl_->runtime().adams(algebra, position, labels, {impl_->max_objects, impl_->timeout});
    auto expression = "Adams(" + std::to_string(position) + "," + format_labels(labels) + "," + algebra + ")";
    auto output = run_lie({expression});
    if (output.empty()) throw std::runtime_error("LiE returned no result for '" + expression + "'");
    std::string text;
    for (const auto& line : output) text += line;
    return parse_lie_decomposition(text, static_cast<int>(labels.size()));
}
Decomposition CharacterDecompositionCache::tensor_decompositions(const std::string& algebra_input, const Decomposition& left, const Decomposition& right) {
    if (left.empty() || right.empty()) return {};
    auto algebra = canonical_cartan_type(algebra_input);
    Labels zero(static_cast<std::size_t>(algebra_rank(algebra)), 0);
    auto scalar = [&](const mpz_class& coefficient, const Decomposition& other) {
        Decomposition result;
        for (const auto& [labels, value] : other) {
            mpz_class product = coefficient * value;
            if (product != 0) result[labels] = std::move(product);
        }
        return result;
    };
    if (left.size() == 1 && left.count(zero)) return scalar(left.at(zero), right);
    if (right.size() == 1 && right.count(zero)) return scalar(right.at(zero), left);
    if (impl_->native_lie)
        return impl_->runtime().tensor(algebra, left, right, {impl_->max_objects, impl_->timeout});
    auto expression = "tensor(" + format_lie_decomposition(left) + "," + format_lie_decomposition(right) + "," + algebra + ")";
    auto output = run_lie({expression});
    std::string text;
    for (const auto& line : output) text += line;
    return parse_lie_decomposition(text, static_cast<int>(zero.size()));
}
std::map<std::string, mpz_class> CharacterDecompositionCache::lookup_singlets(const std::string& algebra, const std::vector<std::string>& keys) {
    auto connection = impl_->db();
    std::map<std::string, mpz_class> result;
    Statement query(connection, "SELECT coefficient FROM singlet_coefficients WHERE algebra=? AND product_key=?");
    for (const auto& key : keys) {
        if (result.count(key)) continue;
        auto found = impl_->singlets.find({algebra, key});
        if (found != impl_->singlets.end()) { result[key] = found->second; continue; }
        query.text(1, algebra); query.text(2, key);
        if (query.row()) result[key] = impl_->singlets[{algebra, key}] = parse_integer(query.column(0));
        query.reset();
    }
    return result;
}
void CharacterDecompositionCache::write_singlets(const std::string& algebra, const std::map<std::string, mpz_class>& entries) {
    if (entries.empty()) return;
    auto connection = impl_->db();
    transaction(connection, [&] {
        Statement statement(connection, "INSERT INTO singlet_coefficients VALUES (?, ?, ?) ON CONFLICT (algebra, product_key) DO NOTHING");
        for (const auto& [key, value] : entries) {
            statement.text(1, algebra); statement.text(2, key); statement.text(3, value.get_str()); statement.run(); statement.reset();
        }
    });
    for (const auto& [key, value] : entries) impl_->singlets[{algebra, key}] = value;
}
std::vector<mpz_class> CharacterDecompositionCache::get_singlet_multiplicities(const std::string& input_algebra,
    int rank, const std::vector<Product>& inputs) {
    auto algebra = canonical_cartan_type(input_algebra); validate_rank(algebra, rank);
    std::vector<std::string> keys;
    std::vector<Product> products;
    for (const auto& input : inputs) {
        products.push_back(canonical_product(algebra, input)); keys.push_back(character_product_key(products.back()));
    }
    auto found = lookup_singlets(algebra, keys);
    std::vector<Product> missing;
    std::vector<std::string> missing_keys;
    std::set<std::string> seen;
    for (std::size_t i = 0; i < keys.size(); ++i)
        if (!found.count(keys[i]) && seen.insert(keys[i]).second) { missing.push_back(products[i]); missing_keys.push_back(keys[i]); }
    if (!missing.empty()) {
        auto values = character_singlets(algebra, rank, missing);
        std::map<std::string, mpz_class> entries;
        for (std::size_t i = 0; i < values.size(); ++i) entries[missing_keys[i]] = values[i];
        write_singlets(algebra, entries); found.insert(entries.begin(), entries.end());
    }
    std::vector<mpz_class> result; result.reserve(keys.size());
    for (const auto& key : keys) result.push_back(found.at(key));
    return result;
}
std::vector<mpz_class> CharacterDecompositionCache::singlet_multiplicities(const std::string& input_algebra,
    int rank, const std::vector<std::vector<Decomposition>>& products) {
    auto algebra = canonical_cartan_type(input_algebra); validate_rank(algebra, rank);
    std::vector<std::string> keys;
    for (const auto& product : products) keys.push_back(decomposed_product_key(product));
    auto found = lookup_singlets(algebra, keys);
    std::vector<std::vector<Decomposition>> missing;
    std::vector<std::string> missing_keys;
    std::set<std::string> seen;
    for (std::size_t i = 0; i < keys.size(); ++i)
        if (!found.count(keys[i]) && seen.insert(keys[i]).second) { missing.push_back(products[i]); missing_keys.push_back(keys[i]); }
    if (!missing.empty()) {
        auto values = compute_singlet_multiplicities(algebra, rank, missing);
        std::map<std::string, mpz_class> entries;
        for (std::size_t i = 0; i < values.size(); ++i) entries[missing_keys[i]] = values[i];
        write_singlets(algebra, entries); found.insert(entries.begin(), entries.end());
    }
    std::vector<mpz_class> result; result.reserve(keys.size());
    for (const auto& key : keys) result.push_back(found.at(key));
    return result;
}
std::vector<mpz_class> CharacterDecompositionCache::character_singlets(const std::string& input_algebra,
    int rank, const std::vector<Product>& products) {
    auto algebra = canonical_cartan_type(input_algebra); validate_rank(algebra, rank); impl_->state();
    const Decomposition identity{{Labels(static_cast<std::size_t>(rank), 0), 1}};
    std::map<Product, std::pair<Product, Product>> plans;
    std::set<Request> pure;
    std::vector<std::pair<Product, Product>> splits;
    std::function<void(const Product&)> plan = [&](const Product& product) {
        if (product.empty() || plans.count(product)) return;
        if (product.size() == 1) {
            pure.insert({algebra, product[0].first, product[0].second}); plans[product] = {}; return;
        }
        auto halves = split_character_product(algebra, product);
        plans[product] = halves; plan(halves.first); plan(halves.second);
    };
    for (const auto& input : products) {
        Product product;
        for (const auto& factor : canonical_product(algebra, input)) if (!trivial(factor.first)) product.push_back(factor);
        std::int64_t count = 0;
        for (const auto& factor : product) count = checked_add(count, factor_count(factor.second));
        auto halves = count <= 1 ? std::make_pair(product, Product{}) : split_character_product(algebra, product);
        splits.push_back(halves); plan(halves.first); plan(halves.second);
    }
    std::vector<Request> requests(pure.begin(), pure.end());
    auto values = get_decompositions(requests);
    std::map<Request, Decomposition> decompositions;
    for (std::size_t i = 0; i < requests.size(); ++i) decompositions.emplace(requests[i], std::move(values[i]));
    std::function<const Decomposition&(const Product&)> expand = [&](const Product& product) -> const Decomposition& {
        if (product.empty()) return identity;
        if (product.size() == 1) return decompositions.at({algebra, product[0].first, product[0].second});
        auto key = std::make_pair(algebra, product);
        auto found = impl_->partial_products.find(key);
        if (found != impl_->partial_products.end()) return found->second;
        const auto& halves = plans.at(product);
        auto value = tensor_decompositions(algebra, expand(halves.first), expand(halves.second));
        return impl_->partial_products.emplace(std::move(key), std::move(value)).first->second;
    };
    std::vector<mpz_class> result; result.reserve(splits.size());
    for (const auto& halves : splits) result.push_back(singlet_pair(algebra, expand(halves.first), expand(halves.second)));
    return result;
}
std::vector<mpz_class> CharacterDecompositionCache::compute_singlet_multiplicities(const std::string& input_algebra,
    int rank, const std::vector<std::vector<Decomposition>>& products) {
    auto algebra = canonical_cartan_type(input_algebra); validate_rank(algebra, rank);
    const Labels zero(static_cast<std::size_t>(rank), 0);
    const Decomposition identity{{zero, 1}};
    std::map<std::string, Decomposition> memo;
    using Iterator = std::vector<Decomposition>::const_iterator;
    std::function<Decomposition(Iterator, Iterator)> expand = [&](Iterator first, Iterator last) -> Decomposition {
        auto size = last - first;
        if (!size) return identity;
        if (size == 1) return *first;
        auto key = decomposed_product_key(std::vector<Decomposition>(first, last));
        auto found = memo.find(key);
        if (found != memo.end()) return found->second;
        auto middle = first + size / 2;
        auto value = tensor_decompositions(algebra, expand(first, middle), expand(middle, last));
        memo[key] = value; return value;
    };
    std::vector<mpz_class> result; result.reserve(products.size());
    for (const auto& product : products) {
        std::vector<Decomposition> factors;
        bool zero_product = false;
        for (const auto& factor : product) {
            Decomposition nonzero;
            for (const auto& [labels, value] : factor) if (value != 0) nonzero[labels] = value;
            if (nonzero.empty()) { zero_product = true; break; }
            factors.push_back(std::move(nonzero));
        }
        if (zero_product) { result.emplace_back(0); continue; }
        if (factors.empty()) { result.emplace_back(1); continue; }
        if (factors.size() == 1) {
            auto found = factors[0].find(zero); result.push_back(found == factors[0].end() ? mpz_class(0) : found->second); continue;
        }
        std::sort(factors.begin(), factors.end(), [](const auto& a, const auto& b) {
            if (a.size() != b.size()) return a.size() < b.size();
            return encode_decomposition(a) < encode_decomposition(b);
        });
        auto middle = factors.cbegin() + static_cast<std::ptrdiff_t>(factors.size() / 2);
        result.push_back(singlet_pair(algebra, expand(factors.cbegin(), middle), expand(middle, factors.cend())));
    }
    return result;
}
ProcessResult CharacterDecompositionCache::execute_lie(const std::vector<std::string>& expressions, bool configure_memory) {
    std::string code;
    if (configure_memory) code = "maxnodes " + std::to_string(impl_->max_nodes) + "\nmaxobjects " + std::to_string(impl_->max_objects);
    for (const auto& expression : expressions) { if (!code.empty()) code += '\n'; code += expression; }
    return run_process(impl_->lie_executable, code, impl_->timeout);
}
std::vector<std::string> CharacterDecompositionCache::run_lie(const std::vector<std::string>& expressions) {
    auto result = execute_lie(expressions, false);
    auto lines = lie_output_lines(result.stdout_text);
    bool retry = result.returncode != 0 || !trim(result.stderr_text).empty();
    for (auto line : lines) {
        std::transform(line.begin(), line.end(), line.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        if (line.find('(') != std::string::npos || line.find("error") != std::string::npos) retry = true;
    }
    if (retry) { result = execute_lie(expressions, true); lines = lie_output_lines(result.stdout_text); }
    if (result.returncode != 0 || !trim(result.stderr_text).empty())
        throw std::runtime_error("LiE failed with code " + std::to_string(result.returncode) + ": " +
            (trim(result.stderr_text).empty() ? trim(result.stdout_text) : trim(result.stderr_text)));
    return lines;
}
} // namespace n2scftdb::characters
