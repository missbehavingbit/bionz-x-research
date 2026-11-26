/* Exact-version native bindings. NOT a hook installer or lifetime observer.
 * ZV-E10 (FW 2.03) first-pass port — UNTESTED. Address model and rebase caveat:
 * see zve10/README.md. */
#ifndef MENU_TOUCH_NATIVE_H
#define MENU_TOUCH_NATIVE_H
#include "menu_touch_policy.h"
struct mt_native_binding {
    volatile uint32_t *epoch;
    struct mt_context context;
    int fault;
};
/* epoch MUST be driven by verified native lifetime/transition hooks. An
   invented constant is not a valid deployment. NULL/zero fails closed.
   Caller dispatches only from the natural input consumer on the UI thread
   (a6400: TID 380; ZV-E10 UI TID unknown — see MT_UI_TID in menu_touch_native.c). */
int mt_native_context(volatile uint32_t *epoch,struct mt_context *out);
int mt_native_bind(struct mt_native_binding *,volatile uint32_t *,struct mt_ops *);
#endif
