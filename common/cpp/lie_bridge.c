/* C-only recovery frame: longjmp never crosses a C++ destructor. */
#define _POSIX_C_SOURCE 200809L
#include "LiE/lie.h"
#include "lie_bridge.h"
#include <pthread.h>
#include <time.h>
#include <stdint.h>

typedef union allocation allocation;
union allocation {
    struct { allocation *previous, *next; int object_allocation; } link;
    long double alignment;
    void *pointer_alignment;
};
static allocation *allocations;
static long object_count, object_limit;
static double deadline;
static double last_poll;
static unsigned int poll_interval;
unsigned int n2_lie_poll_budget = 0;
static jmp_buf recovery;
static n2_lie_result *current_result;
int n2_lie_active = 0;
static pthread_mutex_t runtime_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_once_t fork_once = PTHREAD_ONCE_INIT;
static int fork_error;

unsigned int n2_lie_environment_abi(void) { return N2_LIE_ENVIRONMENT_ABI; }

static void before_fork(void) { pthread_mutex_lock(&runtime_lock); }
static void after_fork(void) { pthread_mutex_unlock(&runtime_lock); }
static void register_fork(void) {
    fork_error = pthread_atfork(before_fork, after_fork, after_fork);
}
static double now(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value)) n2_lie_fail("cannot read monotonic clock");
    return (double)value.tv_sec + (double)value.tv_nsec / 1e9;
}
void n2_lie_fail(const char *message) {
    snprintf(current_result->error, sizeof(current_result->error), "%s", message);
    longjmp(recovery, 1);
}
static void check_deadline_now(void) {
    if (deadline >= 0 && now() >= deadline)
        n2_lie_fail("native LiE calculation timed out");
}
void n2_lie_check_deadline(void) {
    double current = now(), elapsed = current - last_poll;
    double remaining = deadline - current;
    double target = remaining * 0.25;
    unsigned int next;
    if (remaining <= 0) n2_lie_fail("native LiE calculation timed out");
    /* Aim for one clock read per millisecond, tightening near expiration.
       Work between hooks varies, so limit both the batch and its growth.
       Even very fast orbit loops check at least once per 4096 hooks. */
    if (target > 0.001) target = 0.001;
    if (elapsed > 0) {
        double estimate = (double)poll_interval * target / elapsed;
        next = estimate >= 4096 ? 4096 : estimate < 1 ? 1 : (unsigned int)estimate;
    } else next = 4096;
    if (next > poll_interval * 4) next = poll_interval * 4;
    poll_interval = n2_lie_poll_budget = next;
    last_poll = current;
}
void *n2_lie_allocate(size_t size, int is_object) {
    allocation *node;
    n2_lie_poll();
    if (is_object && object_count >= object_limit)
        n2_lie_fail("native LiE object limit exceeded");
    if (size > SIZE_MAX - sizeof(allocation)) n2_lie_fail("native LiE allocation overflow");
    node = (allocation*)malloc(sizeof(allocation) + size);
    if (!node) n2_lie_fail("native LiE allocation failed");
    node->link.previous = NULL;
    node->link.next = allocations;
    node->link.object_allocation = is_object;
    if (allocations) allocations->link.previous = node;
    allocations = node;
    if (is_object) ++object_count;
    return node + 1;
}
void n2_lie_release(void *pointer) {
    allocation *node;
    if (!pointer) return;
    node = (allocation*)pointer - 1;
    if (node->link.previous) node->link.previous->link.next = node->link.next;
    else allocations = node->link.next;
    if (node->link.next) node->link.next->link.previous = node->link.previous;
    if (node->link.object_allocation) --object_count;
    free(node);
}
void n2_lie_free_array(void *pointer) {
    if (n2_lie_active) n2_lie_release(pointer);
    else free(pointer);
}
static void clear_runtime(void) {
    while (allocations) n2_lie_release(allocations + 1);
    n2_lie_reset_groups();
    n2_lie_reset_constants();
    n2_lie_reset_sorting();
    grp = defaultgrp = NULL;
    one = null = minus_one = NULL;
    cmpfn = lex_incr;
}
static poly *import_character(const n2_lie_term *terms, size_t count, long rank) {
    poly *p;
    size_t i;
    if (!count) return poly_null(rank);
    p = mkpoly((long)count, rank);
    for (i = 0; i < count; ++i) {
        bigint *coefficient = mkbigint((long)terms[i].digit_count);
        memcpy(p->elm[i], terms[i].labels, (size_t)rank * sizeof(entry));
        if (terms[i].digit_count)
            memcpy(coefficient->data, terms[i].digits, terms[i].digit_count * sizeof(digit));
        coefficient->size = (short)((long)terms[i].digit_count * terms[i].sign);
        p->coef[i] = coefficient;
        setshared(coefficient);
    }
    /* The caller's map is lexicographically ordered, but reduction also
       normalizes explicit zero coefficients using LiE's current ordering. */
    p = Reduce_pol(p);
    setshared(p);
    return p;
}
void n2_lie_result_free(n2_lie_result *result) {
    size_t i;
    for (i = 0; i < result->count; ++i) {
        free(result->terms[i].labels);
        free(result->terms[i].digits);
    }
    free(result->terms);
    result->terms = NULL; result->count = 0;
}
static void export_character(poly *p, long rank, n2_lie_result *result) {
    index i;
    result->terms = (n2_lie_result_term*)calloc((size_t)p->nrows, sizeof(n2_lie_result_term));
    if (p->nrows && !result->terms) n2_lie_fail("native LiE result allocation failed");
    result->count = (size_t)p->nrows;
    for (i = 0; i < p->nrows; ++i) {
        n2_lie_poll();
        n2_lie_result_term *out = result->terms + i;
        bigint *coefficient = p->coef[i];
        out->digit_count = (size_t)abs(coefficient->size);
        out->sign = coefficient->size < 0 ? -1 : coefficient->size > 0;
        out->labels = (long*)malloc((size_t)rank * sizeof(long));
        out->digits = (unsigned short*)malloc(out->digit_count * sizeof(unsigned short));
        if (!out->labels || (out->digit_count && !out->digits))
            n2_lie_fail("native LiE result allocation failed");
        memcpy(out->labels, p->elm[i], (size_t)rank * sizeof(long));
        if (out->digit_count)
            memcpy(out->digits, coefficient->data, out->digit_count * sizeof(unsigned short));
    }
}
int n2_lie_compute(char family, long rank, long power,
                   const n2_lie_term *left, size_t left_count,
                   const n2_lie_term *right, size_t right_count,
                   long max_objects, double timeout, n2_lie_result *result) {
    int failed, code;
    memset(result, 0, sizeof(*result));
    code = pthread_once(&fork_once, register_fork);
    if (!code) code = fork_error;
    if (!code) code = pthread_mutex_lock(&runtime_lock);
    if (code) {
        snprintf(result->error, sizeof(result->error), "native LiE synchronization failed: %d", code);
        return 1;
    }
    current_result = result;
    deadline = -1;
    n2_lie_poll_budget = 0;
    n2_lie_active = 1;
    failed = setjmp(recovery);
    if (!failed) {
        poly *a, *b, *answer;
        object_limit = max_objects;
        if (timeout >= 0) {
            last_poll = now();
            deadline = last_poll + timeout;
            poll_interval = n2_lie_poll_budget = 64;
        }
        check_deadline_now();
        /* Initialize only the mathematical core. Never read an initfile,
           install signal handlers, or run LiE's command interpreter. */
        cmpfn = lex_incr;
        one = entry2bigint(1); setlonglife(one);
        null = entry2bigint(0); setlonglife(null);
        minus_one = entry2bigint(-1); setlonglife(minus_one);
        grp = defaultgrp = (object)mksimpgrp(family, rank);
        a = import_character(left, left_count, rank);
        if (power) {
            check_deadline_now();
            answer = Adams(power, a);
        }
        else {
            b = import_character(right, right_count, rank);
            check_deadline_now();
            answer = Tensor(a, b);
        }
        check_deadline_now();
        export_character(answer, rank, result);
        check_deadline_now();
    }
    if (failed) n2_lie_result_free(result);
    n2_lie_poll_budget = 0;
    clear_runtime();
    n2_lie_active = 0;
    current_result = NULL;
    pthread_mutex_unlock(&runtime_lock);
    return failed != 0;
}
