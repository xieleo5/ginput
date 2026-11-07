/* SPDX-License-Identifier: GPL-2.0-or-later
 * Guitar Input contributors. Uses Fcitx 4's public module and hook APIs.
 */
#include <stdlib.h>
#include <string.h>
#include <fcitx/fcitx.h>
#include <fcitx/instance.h>
#include <fcitx/module.h>
#include <fcitx/ime.h>
#include <fcitx/candidate.h>
#include <fcitx/hook.h>
#include <fcitx/ui.h>

typedef struct {
    FcitxInstance *instance;
    boolean expanded;
} Panel;

static boolean is_rime(Panel *panel) {
    FcitxIM *im = FcitxInstanceGetCurrentIM(panel->instance);
    return im && im->uniqueName && strcmp(im->uniqueName, "rime") == 0;
}

static FcitxCandidateWordList *candidates(Panel *panel) {
    return FcitxInputStateGetCandidateList(FcitxInstanceGetInputState(panel->instance));
}

static void apply_layout(void *arg) {
    Panel *panel = arg;
    if (!is_rime(panel)) { panel->expanded = false; return; }
    FcitxCandidateWordList *list = candidates(panel);
    if (!FcitxCandidateWordGetListSize(list)) panel->expanded = false;
    FcitxCandidateWordSetLayoutHint(list, panel->expanded ? CLH_Vertical : CLH_Horizontal);
}

static void reset(void *arg) {
    Panel *panel = arg;
    panel->expanded = false;
}

static boolean release_filter(void *arg, FcitxKeySym key, unsigned int state,
                              INPUT_RETURN_VALUE *result) {
    (void)key;
    (void)state;
    (void)result;
    /* fcitx-rime rebuilds candidates directly for unhandled key releases,
     * bypassing the normal update-candidates hook and clearing layout hints. */
    Panel *panel = arg;
    if (is_rime(panel)) apply_layout(panel);
    return false;
}

static boolean key_filter(void *arg, FcitxKeySym key, unsigned int state,
                          INPUT_RETURN_VALUE *result) {
    Panel *panel = arg;
    if ((state & FcitxKeyState_SimpleMask) || !is_rime(panel) || !FcitxCandidateWordGetListSize(candidates(panel)))
        return false;
    if ((key == FcitxKey_Down && !panel->expanded) ||
        (key == FcitxKey_Escape && panel->expanded)) {
        panel->expanded = !panel->expanded;
        apply_layout(panel);
        *result = IRV_DISPLAY_MESSAGE;
        return true;
    }
    if (key == FcitxKey_Return && panel->expanded) {
        FcitxCandidateWordList *list = candidates(panel);
        int count = FcitxCandidateWordGetCurrentWindowSize(list);
        for (int i = 0; i < count; ++i) {
            FcitxCandidateWord *word = FcitxCandidateWordGetByIndex(list, i);
            if (word && (word->wordType & MSG_REGULAR_MASK) == MSG_CANDIATE_CURSOR) {
                *result = FcitxCandidateWordChooseByIndex(list, i);
                return true;
            }
        }
    }
    return false;
}

static void *create(FcitxInstance *instance) {
    Panel *panel = calloc(1, sizeof(*panel));
    if (!panel) return NULL;
    panel->instance = instance;
    FcitxInstanceRegisterPreInputFilter(instance, (FcitxKeyFilterHook){key_filter, panel});
    FcitxInstanceRegisterPostReleaseInputFilter(instance, (FcitxKeyFilterHook){release_filter, panel});
    FcitxInstanceRegisterUpdateCandidateWordHook(instance, (FcitxIMEventHook){apply_layout, panel});
    FcitxInstanceRegisterResetInputHook(instance, (FcitxIMEventHook){reset, panel});
    FcitxInstanceRegisterInputUnFocusHook(instance, (FcitxIMEventHook){reset, panel});
    FcitxInstanceRegisterTriggerOffHook(instance, (FcitxIMEventHook){reset, panel});
    FcitxInstanceRegisterIMChangedHook(instance, (FcitxIMEventHook){reset, panel});
    return panel;
}

FCITX_EXPORT_API int ABI_VERSION = FCITX_ABI_VERSION;
FCITX_EXPORT_API FcitxModule module = {create, NULL, NULL, free, NULL};
