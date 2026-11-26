/* Host runner for the freestanding WiFiSync check suite. */
#include "wifisync_core.h"

#include <stdio.h>

typedef struct ws_check_result {
    const char *name;
    unsigned int pass;
} ws_check_result;

extern ws_check_result g_checks[];
extern unsigned int g_check_count;
extern unsigned int g_check_fail;

int ws_run_checks(void);

int main(void) {
    unsigned int i;
    int failures = ws_run_checks();
    for (i = 0; i < g_check_count; i++)
        printf("%s %s\n", g_checks[i].pass ? "PASS" : "FAIL", g_checks[i].name);
    printf("checks=%u failures=%d\n", g_check_count, failures);
    return failures ? 1 : 0;
}
