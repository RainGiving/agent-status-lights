// SPDX-License-Identifier: MIT
// Claude Code hook client. Forwards the raw hook JSON on stdin to the status
// daemon's Unix socket and exits.
//
// This runs on every PostToolUse, so it is C rather than Python: a Python
// interpreter start would add ~30ms to every single tool call. It must never
// block Claude Code and never fail loudly -- every error path exits 0.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/time.h>

#define MAX_PAYLOAD (256 * 1024)

int main(void) {
    char *buf = malloc(MAX_PAYLOAD);
    if (!buf) return 0;

    size_t len = 0;
    size_t n;
    while (len < MAX_PAYLOAD - 2 && (n = fread(buf + len, 1, MAX_PAYLOAD - 2 - len, stdin)) > 0) {
        len += n;
    }
    if (len == 0) { free(buf); return 0; }
    buf[len++] = '\n';

    const char *home = getenv("HOME");
    if (!home) { free(buf); return 0; }
    char path[512];
    snprintf(path, sizeof(path),
             "%s/Library/Application Support/ClaudeHalo75/status.sock", home);

    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) { free(buf); return 0; }

    // A stalled daemon must not stall Claude Code, so both halves are bounded.
    struct timeval tv = {0, 250000};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, path, sizeof(addr.sun_path) - 1);

    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) == 0) {
        size_t off = 0;
        while (off < len) {
            ssize_t w = write(fd, buf + off, len - off);
            if (w <= 0) break;
            off += (size_t)w;
        }
    }
    close(fd);
    free(buf);
    return 0;
}
