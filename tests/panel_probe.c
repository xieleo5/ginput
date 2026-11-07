/* SPDX-License-Identifier: GPL-2.0-or-later
 * Exercise keyboard events against a small Fcitx API fixture, without a desktop.
 */
#include <assert.h>
#include "../native/panel.c"

static FcitxIM current = {.uniqueName = "rime"};
static FcitxCandidateWord words[5];
static int count = 5, chosen = -1;
static FcitxCandidateLayoutHint hint;
static FcitxKeyFilterHook press_hook, release_hook;
static FcitxIMEventHook update_hook, reset_hook, unfocus_hook, off_hook, changed_hook;

FcitxIM *FcitxInstanceGetCurrentIM(FcitxInstance *instance) {
    (void)instance; return &current;
}
FcitxInputState *FcitxInstanceGetInputState(FcitxInstance *instance) {
    (void)instance; return NULL;
}
FcitxCandidateWordList *FcitxInputStateGetCandidateList(FcitxInputState *input) {
    (void)input; return NULL;
}
int FcitxCandidateWordGetListSize(FcitxCandidateWordList *list) {
    (void)list; return count;
}
int FcitxCandidateWordGetCurrentWindowSize(FcitxCandidateWordList *list) {
    (void)list; return count;
}
void FcitxCandidateWordSetLayoutHint(FcitxCandidateWordList *list, FcitxCandidateLayoutHint value) {
    (void)list; hint = value;
}
FcitxCandidateWord *FcitxCandidateWordGetByIndex(FcitxCandidateWordList *list, int index) {
    (void)list; return &words[index];
}
INPUT_RETURN_VALUE FcitxCandidateWordChooseByIndex(FcitxCandidateWordList *list, int index) {
    (void)list; chosen = index; return IRV_DISPLAY_CANDWORDS;
}
#define REGISTER(name, type, slot) \
    void name(FcitxInstance *instance, type hook) { (void)instance; slot = hook; }
REGISTER(FcitxInstanceRegisterPreInputFilter, FcitxKeyFilterHook, press_hook)
REGISTER(FcitxInstanceRegisterPostReleaseInputFilter, FcitxKeyFilterHook, release_hook)
REGISTER(FcitxInstanceRegisterUpdateCandidateWordHook, FcitxIMEventHook, update_hook)
REGISTER(FcitxInstanceRegisterResetInputHook, FcitxIMEventHook, reset_hook)
REGISTER(FcitxInstanceRegisterInputUnFocusHook, FcitxIMEventHook, unfocus_hook)
REGISTER(FcitxInstanceRegisterTriggerOffHook, FcitxIMEventHook, off_hook)
REGISTER(FcitxInstanceRegisterIMChangedHook, FcitxIMEventHook, changed_hook)

static boolean press(FcitxKeySym key, unsigned int state, INPUT_RETURN_VALUE *result) {
    *result = IRV_TO_PROCESS;
    return press_hook.func(press_hook.arg, key, state, result);
}
int main(void) {
    Panel *panel = create(NULL);
    INPUT_RETURN_VALUE result;
    assert(panel);
    update_hook.func(update_hook.arg);
    assert(hint == CLH_Horizontal);
    assert(press(FcitxKey_Down, 0, &result));
    assert(hint == CLH_Vertical && result == IRV_DISPLAY_MESSAGE);

    /* Regression: Rime directly rebuilds its candidates on key release. */
    hint = CLH_NotSet;
    result = IRV_TO_PROCESS;
    assert(!release_hook.func(release_hook.arg, FcitxKey_Down, 0, &result));
    assert(hint == CLH_Vertical && result == IRV_TO_PROCESS);
    assert(!press(FcitxKey_Down, 0, &result)); /* Engine moves highlight. */
    assert(!press(FcitxKey_Up, 0, &result));
    words[1].wordType = MSG_CANDIATE_CURSOR;
    assert(press(FcitxKey_Return, 0, &result));
    assert(chosen == 1 && result == IRV_DISPLAY_CANDWORDS);
    assert(press(FcitxKey_Escape, 0, &result));
    assert(hint == CLH_Horizontal && count == 5 && result == IRV_DISPLAY_MESSAGE);
    assert(!press(FcitxKey_Return, 0, &result));
    assert(!press(FcitxKey_Down, FcitxKeyState_Ctrl, &result));

    FcitxIMEventHook resets[] = {reset_hook, unfocus_hook, off_hook, changed_hook};
    for (unsigned int i = 0; i < sizeof(resets)/sizeof(resets[0]); ++i) {
        assert(press(FcitxKey_Down, 0, &result));
        resets[i].func(resets[i].arg);
        assert(!panel->expanded);
    }
    count = 0;
    assert(!press(FcitxKey_Down, 0, &result));
    count = 5;
    current.uniqueName = "another-input-method";
    assert(!press(FcitxKey_Down, 0, &result));
    free(panel);
    return 0;
}
