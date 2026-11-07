/* Headless integration probe. Links against the installed librime API. */
#include <rime_api.h>
#include <stdio.h>

int main(int argc, char **argv) {
    if (argc != 4) return 2;
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
    if (!RimeSimulateKeySequence(session, argv[3])) return 4;
    RimeApi *api = rime_get_api();
    RimeCandidateListIterator iterator = {0};
    int count = 0;
    if (api->candidate_list_begin(session, &iterator)) {
        while (count++ < 30 && api->candidate_list_next(&iterator))
            puts(iterator.candidate.text);
        api->candidate_list_end(&iterator);
    }
    RimeDestroySession(session);
    RimeFinalize();
    return 0;
}
