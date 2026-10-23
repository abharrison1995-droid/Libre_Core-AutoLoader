/*
 * Source-only protocol gate for the future Linux offline preflight launcher.
 *
 * This deliberately is NOT a deployable setuid helper.  It validates the
 * fixed 14-argument protocol and then returns EX_UNAVAILABLE before creating
 * namespaces, changing credentials/capabilities, mounting, or executing any
 * child.  The privilege and probe backend must be separately implemented,
 * reviewed, compiled, pinned, and validated on a package-matched native Linux
 * guest before this source can ever be installed setuid.
 *
 * Required implementation boundary (not implemented here): independently
 * inspect the dedicated caller via getpwuid/getgrgid and /etc/subuid plus
 * /etc/subgid; verify the fixed package-tool identities and /proc namespace
 * and mountinfo evidence against this argv; then establish user/group maps
 * and the one-level namespace before an irreversible privilege drop.  Mapping
 * must happen before NoNewPrivs is set because the pinned mapping helpers may
 * need their reviewed privilege transition.  Only after mapping and probe
 * cleanup may the helper close inherited descriptors, clear capabilities,
 * set NoNewPrivs, and sample the final identity/capability state for a report.
 * A report claiming SUPPORTED before the real network/mount/cleanup checks
 * have run would be false evidence, so this stub never emits one.
 */

#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sysexits.h>

#ifndef EXPECTED_PROGRAM
#define EXPECTED_PROGRAM "/usr/local/libexec/librecore-rootless-offline-preflight"
#endif
#define EXPECTED_TARGET_ROOT_PREFIX "/tmp/lc-offline-preflight-"
#define TOKEN_HEX_LENGTH 64
#define MIN_SUBID_COUNT 65536ULL

struct launcher_request {
    const char *probe_root;
    uint64_t uid;
    uint64_t gid;
    uint64_t subuid_start;
    uint64_t subuid_count;
    uint64_t subgid_start;
    uint64_t subgid_count;
    const char *token;
};

static int parse_decimal(const char *text, uint64_t *value) {
    const char *cursor;
    char *end = NULL;
    unsigned long long parsed;

    if (text == NULL || text[0] == '\0' || (text[0] == '0' && text[1] != '\0')) {
        return 0;
    }
    for (cursor = text; *cursor != '\0'; ++cursor) {
        if (*cursor < '0' || *cursor > '9') {
            return 0;
        }
    }
    errno = 0;
    parsed = strtoull(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0') {
        return 0;
    }
    *value = (uint64_t)parsed;
    return 1;
}

static int parse_range(const char *text, uint64_t *start, uint64_t *count) {
    const char *separator;
    char left[32];
    size_t left_length;

    if (text == NULL || (separator = strchr(text, ':')) == NULL || strchr(separator + 1, ':') != NULL) {
        return 0;
    }
    left_length = (size_t)(separator - text);
    if (left_length == 0 || left_length >= sizeof(left) || separator[1] == '\0') {
        return 0;
    }
    memcpy(left, text, left_length);
    left[left_length] = '\0';
    return parse_decimal(left, start) && parse_decimal(separator + 1, count);
}

static int valid_hex_token(const char *text) {
    size_t index;
    if (text == NULL || strlen(text) != TOKEN_HEX_LENGTH) {
        return 0;
    }
    for (index = 0; index < TOKEN_HEX_LENGTH; ++index) {
        if (!((text[index] >= '0' && text[index] <= '9') ||
              (text[index] >= 'a' && text[index] <= 'f'))) {
            return 0;
        }
    }
    return 1;
}

static int valid_probe_root(const char *path) {
    const char *component;
    const char *suffix = "/target-root";
    size_t prefix_length = strlen(EXPECTED_TARGET_ROOT_PREFIX);
    size_t path_length;
    size_t suffix_length = strlen(suffix);

    if (path == NULL || path[0] != '/') {
        return 0;
    }
    path_length = strlen(path);
    if (path_length <= prefix_length + suffix_length ||
        strncmp(path, EXPECTED_TARGET_ROOT_PREFIX, prefix_length) != 0 ||
        strcmp(path + path_length - suffix_length, suffix) != 0) {
        return 0;
    }
    component = path + prefix_length;
    while (component < path + path_length - suffix_length) {
        if (*component == '/' || *component == '.' || *component == '\\') {
            return 0;
        }
        ++component;
    }
    return component == path + path_length - suffix_length;
}

static int parse_request(int argc, char *const argv[], struct launcher_request *request) {
    if (argc != 14 ||
#ifndef ROOTLESS_OFFLINE_LAUNCHER_TESTING
        strcmp(argv[0], EXPECTED_PROGRAM) != 0 ||
#endif
        strcmp(argv[1], "--offline-preflight-v1") != 0 ||
        strcmp(argv[2], "--probe-root") != 0 ||
        strcmp(argv[4], "--uid") != 0 ||
        strcmp(argv[6], "--gid") != 0 ||
        strcmp(argv[8], "--subuid") != 0 ||
        strcmp(argv[10], "--subgid") != 0 ||
        strcmp(argv[12], "--token") != 0) {
        return 0;
    }
    if (!valid_probe_root(argv[3]) ||
        !parse_decimal(argv[5], &request->uid) || request->uid == 0 || request->uid > UINT32_MAX ||
        !parse_decimal(argv[7], &request->gid) || request->gid == 0 || request->gid > UINT32_MAX ||
        !parse_range(argv[9], &request->subuid_start, &request->subuid_count) ||
        !parse_range(argv[11], &request->subgid_start, &request->subgid_count) ||
        request->subuid_start == 0 || request->subgid_start == 0 ||
        request->subuid_count < MIN_SUBID_COUNT || request->subgid_count < MIN_SUBID_COUNT ||
        request->subuid_count > UINT32_MAX || request->subgid_count > UINT32_MAX ||
        request->subuid_start > UINT32_MAX - request->subuid_count ||
        request->subgid_start > UINT32_MAX - request->subgid_count ||
        !valid_hex_token(argv[13])) {
        return 0;
    }
    request->probe_root = argv[3];
    request->token = argv[13];
    return 1;
}

#ifdef ROOTLESS_OFFLINE_LAUNCHER_TESTING
int main(int argc, char **argv) {
    struct launcher_request request = {0};
    return parse_request(argc, argv, &request) ? 0 : EX_USAGE;
}
#else
int main(int argc, char **argv) {
    struct launcher_request request = {0};
    (void)argv;
    if (!parse_request(argc, argv, &request)) {
        fputs("OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: fixed launcher argv rejected\n", stderr);
        return EX_USAGE;
    }
    fputs(
        "OFFLINE_ROOTLESS_LAUNCHER_UNAVAILABLE: namespace/mount backend is not implemented or reviewed\n",
        stderr);
    return EX_UNAVAILABLE;
}
#endif
