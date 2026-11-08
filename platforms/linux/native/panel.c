/* SPDX-License-Identifier: GPL-2.0-or-later
 * Fcitx 4 bridge for a local candidate grid and opt-in local learning. No network.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <spawn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <X11/Xlib.h>
#include <unistd.h>
#include <fcitx/fcitx.h>
#include <fcitx/instance.h>
#include <fcitx/module.h>
#include <fcitx/addon.h>
#include <fcitx/ime.h>
#include <fcitx/frontend.h>
#include <fcitx/candidate.h>
#include <fcitx/hook.h>
#include <fcitx/ui.h>

#define COLUMNS 5
#define ROWS 4
#define MAX_WORDS (COLUMNS * ROWS)
extern char **environ;
typedef struct { char *text; int page, index; } Word;
typedef struct {
    FcitxInstance *instance;
    boolean expanded, dirty, previous, next, ready;
    int selected, count, pages, fd;
    pid_t child;
    uint64_t serial;
    FcitxKeySym consumed;
    FcitxInputContext *context;
    Word words[MAX_WORDS];
    FcitxAddon *addon;
    FcitxUI *original, wrapped;
    Display *display;
    char runtime[PATH_MAX], window_key[96];
    char *pending[256];
    int pending_count;
} Panel;
static Panel *active_panel;
static void json_string(FILE *out, const char *s);
static void input_event(Panel *p, const char *kind, const char *text, FcitxKeySym key, unsigned int state);

static boolean is_rime(Panel *p) {
    FcitxIM *im = FcitxInstanceGetCurrentIM(p->instance);
    return im && im->uniqueName && strcmp(im->uniqueName, "rime") == 0;
}
static FcitxCandidateWordList *candidates(Panel *p) {
    return FcitxInputStateGetCandidateList(FcitxInstanceGetInputState(p->instance));
}
static void clear_words(Panel *p) {
    for (int i = 0; i < p->count; ++i) free(p->words[i].text);
    p->count = p->pages = 0;
}
static boolean send_packet(Panel *p, const char *text, size_t size) {
    if (p->fd < 0) return false;
    if (send(p->fd, text, size, MSG_NOSIGNAL | MSG_DONTWAIT) == (ssize_t)size) return true;
    /* A failed renderer must leave ordinary Fcitx input available. */
    close(p->fd); p->fd = -1; p->ready = p->expanded = false;
    return false;
}
static void hide(Panel *p) {
    ++p->serial;
    if (p->ready) send_packet(p, "{\"hide\":true}", 13);
}
static boolean start_renderer(Panel *p) {
    if (p->fd >= 0) return true;
    if (p->child > 0) {
        if (waitpid(p->child, NULL, WNOHANG) == 0) return false;
        p->child = 0;
    }
    char script[PATH_MAX];
    const char *data = getenv("XDG_DATA_HOME"), *home = getenv("HOME");
    int length = data && *data ? snprintf(script, sizeof(script), "%s/ginput/panel_ui.py", data)
                             : snprintf(script, sizeof(script), "%s/.local/share/ginput/panel_ui.py", home ? home : "");
    if (length < 0 || (size_t)length >= sizeof(script) || access(script, R_OK)) return false;
    int pair[2];
    if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair)) return false;
    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_adddup2(&actions, pair[1], 3);
    posix_spawn_file_actions_addclosefrom_np(&actions, 4);
    char *argv[] = {"/usr/bin/python3", script, "--fd", "3", NULL};
    int error = posix_spawn(&p->child, argv[0], &actions, NULL, argv, environ);
    posix_spawn_file_actions_destroy(&actions);
    close(pair[1]);
    if (error) { close(pair[0]); p->child = 0; return false; }
    p->fd = pair[0]; p->ready = false;
    return true;
}
static void window_identity(Panel *p, char *key, size_t size) {
    Window focus = 0; int revert;
    if (p->display) {
        XGetInputFocus(p->display, &focus, &revert);
        for (int depth = 0; focus > 1 && depth < 16; ++depth) {
            Window root, parent, *children = NULL; unsigned int count;
            if (!XQueryTree(p->display, focus, &root, &parent, &children, &count)) break;
            if (children) XFree(children);
            if (!parent || parent == root) break;
            focus = parent;
        }
    }
    snprintf(key, size, "%ld:%lu", (long)getpid(), focus > 1 ? focus : (unsigned long)FcitxInstanceGetCurrentIC(p->instance));
}
static void input_event(Panel *p, const char *kind, const char *value, FcitxKeySym key, unsigned int state) {
    boolean blocked = !is_rime(p) || (FcitxInstanceGetCurrentCapacity(p->instance) & CAPACITY_PASSWORD);
    char window[96];
    if (blocked) snprintf(window,sizeof(window),"blocked");
    else window_identity(p, window, sizeof(window));
    if (strcmp(window, p->window_key)) {
        snprintf(p->window_key, sizeof(p->window_key), "%s", window);
        if (p->runtime[0]) {
            char path[PATH_MAX], temporary[PATH_MAX];
            int a=snprintf(path,sizeof(path),"%s/active-window",p->runtime);
            int b=snprintf(temporary,sizeof(temporary),"%s/.active-%ld",p->runtime,(long)getpid());
            if (a>0 && b>0 && (size_t)a<sizeof(path) && (size_t)b<sizeof(temporary)) {
                int fd=open(temporary,O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC,0600);
                if (fd>=0) { dprintf(fd,"%s\n",window);close(fd);rename(temporary,path); }
            }
        }
    }
    if (blocked) return;
    start_renderer(p);
    char *packet=NULL;size_t length=0;FILE *out=open_memstream(&packet,&length);
    if (!out) return;
    fprintf(out,"{\"event\":\"%s\",\"window\":\"%s\",\"key\":%u,\"state\":%u,\"composing\":%s,\"text\":",kind,window,(unsigned)key,state,FcitxCandidateWordGetListSize(candidates(p))?"true":"false");
    json_string(out,value);
    if (!strcmp(kind,"commit")) {
        char *preedit=FcitxUIMessagesToCString(FcitxInputStateGetPreedit(FcitxInstanceGetInputState(p->instance)));
        fputs(",\"preedit\":",out);json_string(out,preedit);free(preedit);
    }
    fputs("}",out);fclose(out);
    if (p->ready) send_packet(p,packet,length);
    else if (p->pending_count < 256) { p->pending[p->pending_count++]=packet;return; }
    free(packet);
}
static char *commit_filter(void *arg, const char *text) {
    input_event(arg,"commit",text,0,0);
    return NULL;
}
static void collect(Panel *p) {
    clear_words(p);
    FcitxCandidateWordList *list = candidates(p);
    p->previous = FcitxCandidateWordHasPrev(list);
    int moved = 0, selected = 0;
    for (int row = 0; row < (p->expanded ? ROWS : 1); ++row) {
        int size = FcitxCandidateWordGetCurrentWindowSize(list);
        /* Each engine page is one row. Never silently discard a partial page. */
        if (size > COLUMNS) { p->expanded = false; size = COLUMNS; }
        for (int i = 0; i < size && p->count < MAX_WORDS; ++i) {
            FcitxCandidateWord *word = FcitxCandidateWordGetByIndex(list, i);
            if (!word || !word->strWord) continue;
            if (row == 0 && (word->wordType & MSG_REGULAR_MASK) == MSG_CANDIATE_CURSOR) selected = i;
            p->words[p->count++] = (Word){strdup(word->strWord), row, i};
        }
        ++p->pages;
        p->next = FcitxCandidateWordHasNext(list);
        if (!p->expanded || row == ROWS - 1 || !p->next || size == 0) break;
        if (!FcitxCandidateWordGoNextPage(list)) { p->next = false; break; }
        ++moved;
    }
    while (moved-- > 0) FcitxCandidateWordGoPrevPage(list);
    p->selected = selected;
    p->dirty = false;
    p->context = FcitxInstanceGetCurrentIC(p->instance);
}
static void json_string(FILE *out, const char *s) {
    fputc('"', out);
    for (const unsigned char *c = (const unsigned char *)(s ? s : ""); *c; ++c) {
        if (*c == '"' || *c == '\\') { fputc('\\', out); fputc(*c, out); }
        else if (*c < 32) fprintf(out, "\\u%04x", *c);
        else fputc(*c, out);
    }
    fputc('"', out);
}
static boolean show(Panel *p) {
    if (!p->ready || !is_rime(p) || FcitxCandidateWordGetCurrentWindowSize(candidates(p)) > COLUMNS) return false;
    if (p->dirty) collect(p);
    FcitxInputContext *ic = FcitxInstanceGetCurrentIC(p->instance);
    if (!ic) return false;
    int x = 0, y = 0, w = 0, h = 0;
    FcitxInstanceGetWindowRect(p->instance, ic, &x, &y, &w, &h);
    char *text = NULL; size_t size = 0;
    FILE *out = open_memstream(&text, &size);
    if (!out) return false;
    char *preedit = FcitxUIMessagesToCString(FcitxInputStateGetPreedit(FcitxInstanceGetInputState(p->instance)));
    fprintf(out, "{\"serial\":%llu,\"x\":%d,\"y\":%d,\"cursor_height\":%d,\"expanded\":%s,\"selected\":%d,\"previous\":%s,\"next\":%s,\"preedit\":", (unsigned long long)++p->serial, x, y + h, h, p->expanded ? "true" : "false", p->selected, p->previous ? "true" : "false", p->next ? "true" : "false");
    json_string(out, preedit); free(preedit);
    fputs(",\"words\":[", out);
    for (int i = 0; i < p->count; ++i) {
        if (i) fputc(',', out);
        json_string(out, p->words[i].text);
    }
    fputs("]}", out); fclose(out);
    boolean sent = size < 65536 && send_packet(p, text, size);
    free(text);
    return sent;
}
static void wrapped_show(void *arg) {
    Panel *p = active_panel;
    if (show(p)) {
        if (p->original->CloseInputWindow) p->original->CloseInputWindow(arg);
    } else {
        hide(p);
        if (p->original->ShowInputWindow) p->original->ShowInputWindow(arg);
    }
}
static void wrapped_close(void *arg) {
    Panel *p = active_panel;
    p->expanded = false; p->dirty = true;
    hide(p);
    if (p->original->CloseInputWindow) p->original->CloseInputWindow(arg);
}
static void wrapped_move(void *arg) {
    Panel *p = active_panel;
    if (p->ready && is_rime(p) && FcitxCandidateWordGetListSize(candidates(p))) show(p);
    else if (p->original->MoveInputWindow) p->original->MoveInputWindow(arg);
}
static void attach_ui(void *arg) {
    Panel *p = arg;
    FcitxAddon *addon = FcitxInstanceGetCurrentUI(p->instance);
    if (!addon || !addon->ui || addon == p->addon) return;
    if (p->addon && p->addon->ui == &p->wrapped) p->addon->ui = p->original;
    p->addon = addon; p->original = addon->ui; p->wrapped = *addon->ui;
    p->wrapped.ShowInputWindow = wrapped_show;
    p->wrapped.CloseInputWindow = wrapped_close;
    p->wrapped.MoveInputWindow = wrapped_move;
    addon->ui = &p->wrapped;
}
static void reset(void *arg) {
    Panel *p = arg;
    p->expanded = false; p->dirty = true; p->consumed = 0;
    hide(p);
}
static void update(void *arg) {
    Panel *p = arg;
    p->dirty = true;
    if (!is_rime(p) || !FcitxCandidateWordGetListSize(candidates(p))) p->expanded = false;
}
static int change_page(Panel *p, boolean previous) {
    if (p->dirty) collect(p);
    if (previous ? !p->previous : !p->next) return false;
    int steps = p->expanded ? (previous ? ROWS : p->pages) : 1;
    int shifted = 0;
    for (int i = 0; i < steps; ++i) {
        boolean moved = previous ? FcitxCandidateWordGoPrevPage(candidates(p)) : FcitxCandidateWordGoNextPage(candidates(p));
        if (!moved) break;
        ++shifted;
    }
    p->dirty = true; collect(p);
    return shifted;
}
static INPUT_RETURN_VALUE choose(Panel *p, int index) {
    if (index < 0 || index >= p->count) return IRV_DO_NOTHING;
    ++p->serial;
    Word selected = p->words[index];
    for (int page = 0; page < selected.page; ++page)
        if (!FcitxCandidateWordGoNextPage(candidates(p))) return IRV_DO_NOTHING;
    p->expanded = false; p->dirty = true;
    return FcitxCandidateWordChooseByIndex(candidates(p), selected.index);
}
static boolean key_filter(void *arg, FcitxKeySym key, unsigned int state, INPUT_RETURN_VALUE *result) {
    Panel *p = arg;
    attach_ui(p);
    if ((key >= 32 && key <= 126) || key == FcitxKey_BackSpace || key == FcitxKey_Delete || key == FcitxKey_Escape || key == FcitxKey_Return || key == FcitxKey_Left || key == FcitxKey_Right || key == FcitxKey_Up || key == FcitxKey_Down || key == FcitxKey_Home || key == FcitxKey_End)
        input_event(p,"key","",key,state);
    if (!is_rime(p) || (FcitxInstanceGetCurrentCapacity(p->instance) & CAPACITY_PASSWORD) || (state & FcitxKeyState_SimpleMask) || !FcitxCandidateWordGetListSize(candidates(p)) || FcitxCandidateWordGetCurrentWindowSize(candidates(p)) > COLUMNS) return false;
    if (key == FcitxKey_comma || key == FcitxKey_period) {
        /* One visible page: five compact candidates, or the whole expanded grid. */
        change_page(p, key == FcitxKey_comma);
    } else if (key == FcitxKey_Down && !p->expanded) {
        if (!start_renderer(p)) return false;
        p->expanded = true; p->dirty = true; collect(p);
    } else if (p->expanded) {
        if (p->dirty) collect(p);
        if (key == FcitxKey_Escape) { p->expanded = false; p->dirty = true; }
        else if (key == FcitxKey_Return || key == FcitxKey_space || (key >= FcitxKey_1 && key <= FcitxKey_5)) {
            int index = key >= FcitxKey_1 && key <= FcitxKey_5 ? (p->selected / COLUMNS) * COLUMNS + (int)(key - FcitxKey_1) : p->selected;
            *result = choose(p, index); p->consumed = key; return true;
        } else if (key == FcitxKey_Down) {
            if (p->selected / COLUMNS < (p->count - 1) / COLUMNS) {
                p->selected += COLUMNS;
                if (p->selected >= p->count) p->selected = p->count - 1;
            }
            else if (p->next) { int column = p->selected % COLUMNS; change_page(p, false); p->selected = column < p->count ? column : 0; }
        } else if (key == FcitxKey_Up) {
            if (p->selected >= COLUMNS) p->selected -= COLUMNS;
            else if (p->previous) { int column = p->selected % COLUMNS; int shifted = change_page(p, true); p->selected = (shifted > 0 ? shifted - 1 : 0) * COLUMNS + column; if (p->selected >= p->count) p->selected = p->count - 1; }
        } else if (key == FcitxKey_Left) { if (p->selected > 0) --p->selected; }
        else if (key == FcitxKey_Right) { if (p->selected + 1 < p->count) ++p->selected; }
        else if (key == FcitxKey_Page_Down) change_page(p, false);
        else if (key == FcitxKey_Page_Up) change_page(p, true);
        else { p->dirty = true; return false; }
    } else { p->dirty = true; return false; }
    ++p->serial; p->consumed = key; *result = IRV_DISPLAY_MESSAGE; return true;
}
static boolean release_filter(void *arg, FcitxKeySym key, unsigned int state, INPUT_RETURN_VALUE *result) {
    (void)state;
    Panel *p = arg;
    if (p->consumed == key) { p->consumed = 0; *result = IRV_DO_NOTHING; return true; }
    return false;
}
static void set_fd(void *arg) {
    Panel *p = arg;
    if (p->fd < 0) return;
    FD_SET(p->fd, FcitxInstanceGetReadFDSet(p->instance));
    if (p->fd > FcitxInstanceGetMaxFD(p->instance)) FcitxInstanceSetMaxFD(p->instance, p->fd);
}
static void process_event(void *arg) {
    Panel *p = arg;
    if (p->fd < 0) return;
    char buffer[128]; ssize_t size;
    while ((size = recv(p->fd, buffer, sizeof(buffer) - 1, MSG_DONTWAIT)) > 0) {
        buffer[size] = 0;
        if (!strcmp(buffer, "ready")) { p->ready = true; p->dirty = true;
            for (int i=0;i<p->pending_count;++i) { send_packet(p,p->pending[i],strlen(p->pending[i]));free(p->pending[i]); }
            p->pending_count=0; attach_ui(p); FcitxUIUpdateInputWindow(p->instance); continue; }
        unsigned long long serial; int value; char command[16];
        if (sscanf(buffer, "%15s %llu %d", command, &serial, &value) != 3 || serial != p->serial || p->dirty || !is_rime(p) || p->context != FcitxInstanceGetCurrentIC(p->instance) || !FcitxCandidateWordGetListSize(candidates(p))) continue;
        if (!strcmp(command, "select") && value >= 0 && value < p->count) {
            INPUT_RETURN_VALUE result = choose(p, value);
            FcitxInstanceDoInputCallback(p->instance, result, FCITX_PRESS_KEY, 0, FcitxKey_None, 0);
        } else if (!strcmp(command, "page") && (value == -1 || value == 1)) {
            change_page(p, value == -1); FcitxUIUpdateInputWindow(p->instance);
        }
    }
    if (size == 0 || (size < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
        close(p->fd); p->fd = -1; p->ready = p->expanded = false; p->dirty = true;
        FcitxUIUpdateInputWindow(p->instance);
    }
}
static void destroy(void *arg) {
    Panel *p = arg;
    if (p->addon && p->addon->ui == &p->wrapped) p->addon->ui = p->original;
    if (p->fd >= 0) close(p->fd);
    if (p->child > 0) waitpid(p->child, NULL, WNOHANG);
    if (p->display) XCloseDisplay(p->display);
    for (int i=0;i<p->pending_count;++i) free(p->pending[i]);
    clear_words(p); free(p); active_panel = NULL;
}
static void *create(FcitxInstance *instance) {
    Panel *p = calloc(1, sizeof(*p));
    if (!p) return NULL;
    p->instance = instance; p->fd = -1; p->dirty = true; active_panel = p;
    p->display=XOpenDisplay(NULL);
    const char *runtime=getenv("XDG_RUNTIME_DIR"), *home=getenv("HOME");
    int length=runtime && *runtime ? snprintf(p->runtime,sizeof(p->runtime),"%s/ginput",runtime)
                                  : snprintf(p->runtime,sizeof(p->runtime),"%s/.cache/ginput",home?home:"");
    if (length<0 || (size_t)length>=sizeof(p->runtime) || (mkdir(p->runtime,0700) && errno!=EEXIST)) p->runtime[0]=0;
    FcitxInstanceRegisterCommitFilter(instance,(FcitxStringFilterHook){commit_filter,p});
    FcitxInstanceRegisterPreInputFilter(instance, (FcitxKeyFilterHook){key_filter, p});
    FcitxInstanceRegisterPreReleaseInputFilter(instance, (FcitxKeyFilterHook){release_filter, p});
    FcitxInstanceRegisterUpdateCandidateWordHook(instance, (FcitxIMEventHook){update, p});
    FcitxInstanceRegisterResetInputHook(instance, (FcitxIMEventHook){reset, p});
    FcitxInstanceRegisterInputUnFocusHook(instance, (FcitxIMEventHook){reset, p});
    FcitxInstanceRegisterTriggerOffHook(instance, (FcitxIMEventHook){reset, p});
    FcitxInstanceRegisterIMChangedHook(instance, (FcitxIMEventHook){reset, p});
    FcitxInstanceAddTimeout(instance, 0, attach_ui, p);
    start_renderer(p);
    return p;
}
FCITX_EXPORT_API int ABI_VERSION = FCITX_ABI_VERSION;
FCITX_EXPORT_API FcitxModule module = {create, set_fd, process_event, destroy, NULL};
