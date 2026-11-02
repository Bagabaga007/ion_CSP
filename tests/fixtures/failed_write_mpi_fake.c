/* Offline-only MPI payload for checking failed-write tracing without VASP. */
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/types.h>
#include <unistd.h>

int main(void) {
    const char *rank_text = getenv("PMI_RANK");
    const char *token = getenv("ION_CSP_VASP_DIAGNOSTIC_TOKEN");
    if (!rank_text || !token || strlen(token) != 32) return 101;
    int rank = atoi(rank_text);
    if (rank < 0 || rank > 3) return 102;
    struct rlimit limit = {1024, 1024};
    if (setrlimit(RLIMIT_FSIZE, &limit) != 0) return 103;
    if (!getenv("ION_CSP_FAKE_USE_HELPER")) {
        char marker[256];
        if (snprintf(marker, sizeof marker,
                     ".ion_csp_vasp_limit_ranks/r-%s-%d-p%ld-1024-1024-"
                     "00000000000000000000000000000000.rec",
                     token, rank, (long)getpid()) >= (int)sizeof marker) return 104;
        int record = open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600);
        if (record < 0) return 105;
        close(record);
    }
    if (rank != 0) {
        usleep(700000);
        return 0;
    }
    int fd = open("rank-0-target.bin", O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) return 106;
    char bytes[1024] = {0};
    if (write(fd, bytes, sizeof bytes) != sizeof bytes) return 107;
    usleep(400000);
    /* The native SIGXFSZ default disposition is intentionally preserved. */
    if (write(fd, bytes, 1) >= 0) return 109;
    return 108;
}
