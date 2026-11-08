/* SPDX-License-Identifier: GPL-2.0-or-later
 * Candidate-page fixture: verifies that the grid selects the engine's true index.
 */
#define _GNU_SOURCE
#include <assert.h>
#include "../native/panel.c"
static FcitxIM current = {.uniqueName = "rime"};
static FcitxCandidateWord engine_words[5];
static char labels[47][16];
static int total = 47, engine_page, chosen = -1;
static FcitxCapacityFlags capacity=CAPACITY_NONE;
FcitxCapacityFlags FcitxInstanceGetCurrentCapacity(FcitxInstance *i) {(void)i;return capacity;}
static fd_set fds;
static FcitxUI ui;
static FcitxAddon addon = {.ui = &ui};
FcitxIM *FcitxInstanceGetCurrentIM(FcitxInstance *i) { (void)i; return &current; }
FcitxInputState *FcitxInstanceGetInputState(FcitxInstance *i) { (void)i; return NULL; }
FcitxInputContext *FcitxInstanceGetCurrentIC(FcitxInstance *i) { (void)i; return (FcitxInputContext *)&current; }
FcitxCandidateWordList *FcitxInputStateGetCandidateList(FcitxInputState *i) { (void)i; return NULL; }
FcitxMessages *FcitxInputStateGetPreedit(FcitxInputState *i) { (void)i; return NULL; }
char *FcitxUIMessagesToCString(FcitxMessages *m) { (void)m; return strdup("ni hao"); }
int FcitxCandidateWordGetCurrentWindowSize(FcitxCandidateWordList *l) { (void)l; int n=total-engine_page*5; return n<5?n:5; }
int FcitxCandidateWordGetListSize(FcitxCandidateWordList *l) { return FcitxCandidateWordGetCurrentWindowSize(l); }
boolean FcitxCandidateWordHasPrev(FcitxCandidateWordList *l) { (void)l; return engine_page>0; }
boolean FcitxCandidateWordHasNext(FcitxCandidateWordList *l) { (void)l; return (engine_page+1)*5<total; }
boolean FcitxCandidateWordGoPrevPage(FcitxCandidateWordList *l) { if (!FcitxCandidateWordHasPrev(l)) return false; --engine_page; return true; }
boolean FcitxCandidateWordGoNextPage(FcitxCandidateWordList *l) { if (!FcitxCandidateWordHasNext(l)) return false; ++engine_page; return true; }
FcitxCandidateWord *FcitxCandidateWordGetByIndex(FcitxCandidateWordList *l, int index) {
    (void)l; engine_words[index].strWord=labels[engine_page*5+index];
    engine_words[index].wordType=index==0?MSG_CANDIATE_CURSOR:MSG_OTHER;
    return &engine_words[index];
}
INPUT_RETURN_VALUE FcitxCandidateWordChooseByIndex(FcitxCandidateWordList *l, int index) { (void)l; chosen=engine_page*5+index; return IRV_DISPLAY_CANDWORDS; }
void FcitxInstanceGetWindowRect(FcitxInstance *i, FcitxInputContext *c, int *x,int *y,int *w,int *h) { (void)i;(void)c;*x=*y=*w=*h=0; }
FcitxAddon *FcitxInstanceGetCurrentUI(FcitxInstance *i) { (void)i;return &addon; }
void FcitxUIUpdateInputWindow(FcitxInstance *i) { (void)i; }
INPUT_RETURN_VALUE FcitxInstanceDoInputCallback(FcitxInstance *i, INPUT_RETURN_VALUE r, FcitxKeyEventType e, unsigned long t, FcitxKeySym s, unsigned int st) { (void)i;(void)e;(void)t;(void)s;(void)st;return r; }
fd_set *FcitxInstanceGetReadFDSet(FcitxInstance *i) { (void)i; return &fds; }
int FcitxInstanceGetMaxFD(FcitxInstance *i) { (void)i;return 0; }
void FcitxInstanceSetMaxFD(FcitxInstance *i,int n) { (void)i;(void)n; }
uint64_t FcitxInstanceAddTimeout(FcitxInstance *i,long ms,FcitxTimeoutCallback cb,void *a) { (void)i;(void)ms;(void)cb;(void)a;return 0; }
#define REGISTER(name,type) void name(FcitxInstance *i,type h) {(void)i;(void)h;}
REGISTER(FcitxInstanceRegisterCommitFilter,FcitxStringFilterHook)
REGISTER(FcitxInstanceRegisterPreInputFilter,FcitxKeyFilterHook)
REGISTER(FcitxInstanceRegisterPreReleaseInputFilter,FcitxKeyFilterHook)
REGISTER(FcitxInstanceRegisterUpdateCandidateWordHook,FcitxIMEventHook)
REGISTER(FcitxInstanceRegisterResetInputHook,FcitxIMEventHook)
REGISTER(FcitxInstanceRegisterInputUnFocusHook,FcitxIMEventHook)
REGISTER(FcitxInstanceRegisterTriggerOffHook,FcitxIMEventHook)
REGISTER(FcitxInstanceRegisterIMChangedHook,FcitxIMEventHook)
static void press(Panel *p,FcitxKeySym key) {
    INPUT_RETURN_VALUE result=IRV_TO_PROCESS;
    assert(key_filter(p,key,0,&result));
    /* Handled releases must never reset the grid selection in the engine. */
    assert(release_filter(p,key,0,&result));
}
int main(void) {
    for (int i=0;i<47;++i) snprintf(labels[i],sizeof(labels[i]),"word%02d",i);
    int pair[2]; assert(!socketpair(AF_UNIX,SOCK_SEQPACKET,0,pair));
    Panel p={.fd=pair[0],.ready=true,.dirty=true}; active_panel=&p;
    /* Sensitive fields and other engines must never transmit key or commit data. */
    char event[1024];
    capacity=CAPACITY_PASSWORD;
    input_event(&p,"key","",'p',0);commit_filter(&p,"private");
    assert(recv(pair[1],event,sizeof(event),MSG_DONTWAIT)==-1 && errno==EAGAIN);
    assert(!strcmp(p.window_key,"blocked"));
    capacity=CAPACITY_NONE;
    input_event(&p,"key","",'n',0);
    int size=recv(pair[1],event,sizeof(event)-1,MSG_DONTWAIT);assert(size>0);event[size]=0;
    assert(strstr(event,"\"event\":\"key\"") && strstr(event,"\"key\":110"));
    commit_filter(&p,"你好");
    size=recv(pair[1],event,sizeof(event)-1,MSG_DONTWAIT);assert(size>0);event[size]=0;
    assert(strstr(event,"你好") && strstr(event,"ni hao"));
    press(&p,FcitxKey_comma); assert(engine_page==0 && !p.expanded);
    press(&p,FcitxKey_period); assert(engine_page==1 && p.count==5 && !p.expanded);
    press(&p,FcitxKey_comma); assert(engine_page==0 && !p.expanded);
    press(&p,FcitxKey_Down);
    assert(p.expanded && p.count==20 && p.pages==4 && engine_page==0);
    assert(!strcmp(p.words[19].text,"word19"));
    press(&p,FcitxKey_period); assert(engine_page==4 && p.count==20 && p.expanded);
    press(&p,FcitxKey_period); assert(engine_page==8 && p.count==7);
    press(&p,FcitxKey_period); assert(engine_page==8 && p.count==7);
    press(&p,FcitxKey_comma); assert(engine_page==4 && p.count==20);
    press(&p,FcitxKey_comma); assert(engine_page==0 && p.count==20);
    press(&p,FcitxKey_comma); assert(engine_page==0);
    press(&p,FcitxKey_Down); assert(p.selected==5);
    press(&p,FcitxKey_Right); assert(p.selected==6);
    press(&p,FcitxKey_Return); assert(chosen==6 && !p.expanded);
    engine_page=0; press(&p,FcitxKey_Down);
    press(&p,FcitxKey_Page_Down); assert(engine_page==4 && !strcmp(p.words[0].text,"word20"));
    press(&p,FcitxKey_Page_Down); assert(engine_page==8 && p.count==7 && !p.next);
    press(&p,FcitxKey_Page_Up); assert(engine_page==4 && p.count==20);
    press(&p,FcitxKey_Page_Up); assert(engine_page==0);
    press(&p,FcitxKey_Down); press(&p,FcitxKey_3); assert(chosen==7);
    press(&p,FcitxKey_Down);
    INPUT_RETURN_VALUE punctuation_result=IRV_TO_PROCESS;
    assert(!key_filter(&p,FcitxKey_equal,0,&punctuation_result));
    assert(!key_filter(&p,FcitxKey_minus,0,&punctuation_result));
    press(&p,FcitxKey_Escape);
    /* Expanding from an ordinary page near the start must move Up backwards. */
    engine_page=2; press(&p,FcitxKey_Down); press(&p,FcitxKey_Up);
    assert(engine_page==0 && p.selected==5 && !strcmp(p.words[5].text,"word05"));
    press(&p,FcitxKey_Escape);
    /* A short final row clamps to its last candidate, rather than getting stuck. */
    engine_page=8; press(&p,FcitxKey_Down);
    for (int i=0;i<4;++i) press(&p,FcitxKey_Right);
    press(&p,FcitxKey_Down); assert(p.selected==6);
    press(&p,FcitxKey_Escape);
    engine_page=0; press(&p,FcitxKey_Down); press(&p,FcitxKey_Escape);
    assert(!p.expanded && total==47 && engine_page==0);
    INPUT_RETURN_VALUE result=IRV_TO_PROCESS;
    assert(!key_filter(&p,FcitxKey_Down,FcitxKeyState_Ctrl,&result));
    assert(!key_filter(&p,FcitxKey_period,FcitxKeyState_Ctrl,&result));
    total=0;
    assert(!key_filter(&p,FcitxKey_comma,0,&result));
    assert(!key_filter(&p,FcitxKey_period,0,&result));
    total=0; assert(!key_filter(&p,FcitxKey_Down,0,&result)); total=47;
    current.uniqueName="another-input-method";
    assert(!key_filter(&p,FcitxKey_Down,0,&result)); current.uniqueName="rime";
    press(&p,FcitxKey_Down); reset(&p); assert(!p.expanded);
    /* Stale mouse messages cannot commit words after another frame or reset. */
    collect(&p); p.serial=4; p.dirty=false; chosen=-1;
    assert(send(pair[1],"select 3 2",10,0)==10); process_event(&p); assert(chosen==-1);
    p.dirty=true; assert(send(pair[1],"select 4 2",10,0)==10); process_event(&p); assert(chosen==-1);
    p.dirty=false; assert(send(pair[1],"select 4 2",10,0)==10); process_event(&p); assert(chosen==2);
    close(pair[1]); process_event(&p); assert(p.fd==-1 && !p.ready && !p.expanded);
    clear_words(&p);
    if (addon.ui==&p.wrapped) addon.ui=p.original;
    return 0;
}
