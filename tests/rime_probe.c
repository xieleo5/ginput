/* Headless integration probe. Links against the installed librime API. */
#include <rime_api.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc != 4 && !(argc == 6 && (strcmp(argv[4], "--commit") == 0 || strcmp(argv[4], "--type") == 0))) return 2;
    RIME_STRUCT(RimeTraits, traits);
    traits.shared_data_dir = argv[1];
    traits.user_data_dir = argv[2];
    traits.app_name = "rime.ginput-test";
    traits.min_log_level = 2;
    RimeSetup(&traits);
    RimeInitialize(&traits);
    RimeSessionId session = RimeCreateSession();
    if (!session || !RimeSelectSchema(session, "ginput")) return 3;
    RimeSetOption(session, "ascii_mode", False);
    if (argc==6 && !strcmp(argv[4],"--type")) {
        char output[8192]={0};
        for (const unsigned char *key=(const unsigned char*)argv[3];*key;++key) {
            int handled=RimeProcessKey(session,*key,0);
            RIME_STRUCT(RimeCommit, commit);
            if (RimeGetCommit(session,&commit)) {
                strncat(output,commit.text,sizeof(output)-strlen(output)-1);
                RimeFreeCommit(&commit);
            }
            if (!handled && strlen(output)+1<sizeof(output)) {size_t n=strlen(output);output[n]=*key;output[n+1]=0;}
        }
        puts(output);
        int result=strcmp(output,argv[5])!=0;
        RimeDestroySession(session);RimeFinalize();return result;
    }
    if (!RimeSimulateKeySequence(session, argv[3])) return 4;
    RimeApi *api = rime_get_api();
    RimeCandidateListIterator iterator = {0};
    int count = 0, selected = -1, result = 0;
    if (api->candidate_list_begin(session, &iterator)) {
        while (count < 30 && api->candidate_list_next(&iterator)) {
            if (argc == 4) puts(iterator.candidate.text);
            else if (strcmp(iterator.candidate.text, argv[5]) == 0 && selected < 0)
                selected = count;
            count++;
        }
        api->candidate_list_end(&iterator);
    }
    if (argc == 6) {
        RIME_STRUCT(RimeCommit, commit);
        if (selected < 0 || !api->select_candidate(session, selected)) {
            result = 5;
        } else if (!RimeGetCommit(session, &commit)) {
            fprintf(stderr, "Selection did not commit the entire input: %s\n", api->get_input(session));
            result = 6;
        } else {
            const char *remaining = api->get_input(session);
            puts(commit.text);
            if (strcmp(commit.text, argv[5]) != 0 || (remaining && *remaining)) result = 7;
            RimeFreeCommit(&commit);
        }
    }
    RimeDestroySession(session);
    RimeFinalize();
    return result;
}
