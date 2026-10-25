#ifndef N2SCFTDB_LIE_BRIDGE_H
#define N2SCFTDB_LIE_BRIDGE_H
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif
/* Private C boundary. Digits are little-endian base 2^15, including signs. */
typedef struct {
    const long *labels;
    const unsigned short *digits;
    size_t digit_count;
    int sign;
} n2_lie_term;
typedef struct {
    long *labels;
    unsigned short *digits;
    size_t digit_count;
    int sign;
} n2_lie_result_term;
typedef struct {
    n2_lie_result_term *terms;
    size_t count;
    char error[512];
} n2_lie_result;

int n2_lie_compute(char family, long rank, long adams_power,
                   const n2_lie_term *left, size_t left_count,
                   const n2_lie_term *right, size_t right_count,
                   long max_objects, double timeout, n2_lie_result *result);
void n2_lie_result_free(n2_lie_result *result);
/* Version 1 requires the build to hide all LiE state and internal functions.
   An isolated loader must reject older libraries before running calculations. */
#define N2_LIE_ENVIRONMENT_ABI 1u
unsigned int n2_lie_environment_abi(void);

/* Hooks used only by the shared-library build of the upstream C sources. */
extern int n2_lie_active;
void *n2_lie_allocate(size_t size, int object_allocation);
void n2_lie_release(void *pointer);
void n2_lie_free_array(void *pointer);
/* Each library instance serializes its mathematical core. Zero disables polling outside native
   calculations or with an unlimited timeout. Keep hot-loop calls inlined. */
extern unsigned int n2_lie_poll_budget;
void n2_lie_check_deadline(void);
static __inline__ void n2_lie_poll(void) {
    if (n2_lie_poll_budget && --n2_lie_poll_budget == 0)
        n2_lie_check_deadline();
}
void n2_lie_fail(const char *message);
void n2_lie_reset_groups(void);
void n2_lie_reset_constants(void);
void n2_lie_reset_sorting(void);
#ifdef __cplusplus
}
#endif
#endif
