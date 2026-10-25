// Standalone regression executable; also suitable for ASan/UBSan builds.
#include "../../common/cpp/lie.hpp"
#include <cassert>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <memory>

using namespace n2scftdb::lie;

int main() {
    const auto psi2 = adams("A1", 2, Labels{1});
    assert(psi2 == (Decomposition{{{0}, -1}, {{2}, 1}}));
    for (int i = 0; i < 100; ++i) {
        assert(adams("A1", 2, Labels{1}) == psi2);
        assert(tensor("A2", Labels{1, 0}, Labels{0, 1}) ==
               (Decomposition{{{0, 0}, 1}, {{1, 1}, 1}}));
        assert(adams("C2", 2, Labels{1, 0}) ==
               (Decomposition{{{0, 0}, -1}, {{0, 1}, -1}, {{2, 0}, 1}}));
    }
    // Adams is additive on virtual characters; composition multiplies powers.
    assert(adams("A1", 2, psi2) == adams("A1", 4, Labels{1}));
    assert(adams("A1", 1, psi2) == psi2);
    assert(adams("A1", 2, Decomposition{}).empty());
    assert(tensor("A1", psi2, Decomposition{{{1}, 1}}) == (Decomposition{{{3}, 1}}));
    mpz_class huge = 1;
    mpz_mul_2exp(huge.get_mpz_t(), huge.get_mpz_t(), 20000);
    assert(tensor("A1", Decomposition{{{1}, -huge}}, Decomposition{{{1}, huge}}) ==
           (Decomposition{{{0}, -huge * huge}, {{2}, -huge * huge}}));
    // A failure raised inside upstream bigint arithmetic must recover too.
    mpz_mul_2exp(huge.get_mpz_t(), huge.get_mpz_t(), 280000);
    try { tensor("A1", Decomposition{{{1}, huge}}, Decomposition{{{1}, huge}}); assert(false); }
    catch (const std::runtime_error&) {}
    assert(adams("A1", 2, Labels{1}) == psi2);
    for (auto options : {Options{3, 1}, Options{100, 0}}) {
        try { adams("A1", 2, Labels{1}, options); assert(false); }
        catch (const std::runtime_error&) {}
        assert(adams("A1", 2, Labels{1}) == psi2);
    }
    try { adams("E8", 2, Labels(8, 1), Options{9999999, 0.002}); assert(false); }
    catch (const std::runtime_error&) {}
    try { adams("A1", 0, Labels{1}); assert(false); }
    catch (const std::invalid_argument&) {}
    try { tensor("A1", Labels{-1}, Labels{1}); assert(false); }
    catch (const std::invalid_argument&) {}
    try { adams("E9", 2, Labels(9, 1)); assert(false); }
    catch (const std::invalid_argument&) {}
    std::vector<std::thread> threads;
    for (int i = 0; i < 8; ++i) threads.emplace_back([&] {
        for (int j = 0; j < 20; ++j) assert(adams("A1", 2, Labels{1}) == psi2);
    });
    for (auto& thread : threads) thread.join();
    // Keep 32 distinct instances alive at once, beyond dlmopen's namespace
    // limit. They must agree with the original runtime and each other.
    std::vector<std::unique_ptr<Environment>> environments;
    for (int i = 0; i < 32; ++i) environments.push_back(std::make_unique<Environment>());
    threads.clear();
    for (auto& environment : environments) threads.emplace_back([&, ptr = environment.get()] {
        for (int j = 0; j < 20; ++j) {
            assert(ptr->adams("A1", 2, Labels{1}) == psi2);
            assert(ptr->adams("A1", 2, psi2) == adams("A1", 4, Labels{1}));
            assert(ptr->tensor("A2", Labels{1, 0}, Labels{0, 1}) ==
                   (Decomposition{{{0, 0}, 1}, {{1, 1}, 1}}));
            assert(ptr->tensor("A1", psi2, Decomposition{{{1}, 1}}) == (Decomposition{{{3}, 1}}));
        }
        // Failure in upstream arithmetic and a deadline must recover in the
        // SAME environment, while the other environments keep calculating.
        try { ptr->tensor("A1", Decomposition{{{1}, huge}}, Decomposition{{{1}, huge}}); assert(false); }
        catch (const std::runtime_error&) {}
        try { ptr->adams("E8", 2, Labels(8, 1), Options{9999999, 0.002}); assert(false); }
        catch (const std::runtime_error&) {}
        assert(ptr->adams("A1", 2, Labels{1}) == psi2);
    });
    for (auto& thread : threads) thread.join();
    auto retained = environments.front()->adams("A1", 2, Labels{1});
    environments.clear();
    assert(retained == psi2);
    // Previously returned objects must survive all subsequent runtime resets.
    assert(psi2 == (Decomposition{{{0}, -1}, {{2}, 1}}));
    std::cout << "native wrapper checks passed\n";
}
