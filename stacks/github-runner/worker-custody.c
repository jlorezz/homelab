#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <sys/types.h>
#include <poll.h>
#include <signal.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <time.h>
#include <limits.h>

#ifndef CUSTODY_HELPER
#define CUSTODY_HELPER "/usr/local/lib/agent-platform/custody.py"
#endif
#ifndef CUSTODY_PYTHON
#define CUSTODY_PYTHON "/usr/bin/python3"
#endif

static void fail(pid_t helper) {
    if (helper > 0) {
        kill(helper, SIGKILL);
        /* Error cleanup must also be bounded if a killed claimer is stuck in
         * kernel I/O. Parent-death SIGKILL remains pending after our _exit. */
        for (int attempt = 0; attempt < 100; attempt++) {
            pid_t result = waitpid(helper, NULL, WNOHANG);
            if (result == helper || (result < 0 && errno == ECHILD)) break;
            struct timespec pause = {.tv_nsec = 1000000};
            nanosleep(&pause, NULL);
        }
    }
    static const char message[] = "CI Worker kernel admission failed\n";
    (void)!write(STDERR_FILENO, message, sizeof(message) - 1);
    _exit(125);
}

static long long millis(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) fail(-1);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* Nothing is wrapped in the persistent runner directory. Only the actual native
 * apphost holds these CLOEXEC descriptors; exec'd job children cannot retain them.
 * Actual .NET apphost survival is a required live gate, beyond the offline tests.
 */
__attribute__((constructor)) static void admit_worker(void) {
    char exe[PATH_MAX];
    ssize_t size = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    if (size < 0 || (size_t)size >= sizeof(exe) - 1) fail(-1);
    exe[size] = '\0';
    const char *base = strrchr(exe, '/');
    if (!base || strcmp(base + 1, "Runner.Worker") || !getenv("AGENT_CI_PROFILE")) return;

    double seconds = 1800;
    const char *setting = getenv("AGENT_CI_QUEUE_TIMEOUT");
    if (setting) {
        char *end;
        seconds = strtod(setting, &end);
        if (!*setting || *end || !(seconds > 0 && seconds <= 1800)) fail(-1);
    }
    /* One bounded deadline covers queueing, receipt, ACK and helper exit. */
    long long deadline = millis() + (long long)(seconds * 1000) + 2000;
    int pair[2];
    if (socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, pair)) fail(-1);
    pid_t owner = getpid();
    pid_t helper = fork();
    if (helper < 0) fail(-1);
    if (!helper) {
        close(pair[0]);
        /* Kill a queued claimer if the Worker dies, with the fork/prctl race closed. */
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != owner ||
            fcntl(pair[1], F_SETFD, 0)) _exit(125);
        char fd[32];
        snprintf(fd, sizeof(fd), "%d", pair[1]);
        execl(CUSTODY_PYTHON, CUSTODY_PYTHON, CUSTODY_HELPER, fd, (char *)NULL);
        _exit(125);
    }
    close(pair[1]);
    struct pollfd event = {.fd = pair[0], .events = POLLIN};
    int ready;
    do {
        long long left = deadline - millis();
        if (left <= 0) fail(helper);
        ready = poll(&event, 1, (int)left);
    } while (ready < 0 && errno == EINTR);
    if (ready <= 0 || !(event.revents & POLLIN)) fail(helper);

    char payload[5];
    union { struct cmsghdr align; char bytes[CMSG_SPACE(8 * sizeof(int))]; } control;
    struct iovec iov = {.iov_base = payload, .iov_len = sizeof(payload)};
    struct msghdr message = {.msg_iov = &iov, .msg_iovlen = 1,
                            .msg_control = control.bytes, .msg_controllen = sizeof(control.bytes)};
    ssize_t received = recvmsg(pair[0], &message, MSG_CMSG_CLOEXEC | MSG_DONTWAIT);
    if (received != 4 || message.msg_controllen > sizeof(control.bytes) ||
        memcmp(payload, "CIC1", 4) ||
        (message.msg_flags & (MSG_CTRUNC | MSG_TRUNC))) fail(helper);
    int count = 0;
    for (struct cmsghdr *cmsg = CMSG_FIRSTHDR(&message); cmsg;
         cmsg = CMSG_NXTHDR(&message, cmsg)) {
        if (cmsg->cmsg_len < CMSG_LEN(0) ||
            (char *)cmsg + cmsg->cmsg_len > control.bytes + message.msg_controllen ||
            cmsg->cmsg_level != SOL_SOCKET || cmsg->cmsg_type != SCM_RIGHTS ||
            (cmsg->cmsg_len - CMSG_LEN(0)) % sizeof(int)) fail(helper);
        size_t n = (cmsg->cmsg_len - CMSG_LEN(0)) / sizeof(int);
        int *fds = (int *)CMSG_DATA(cmsg);
        for (size_t i = 0; i < n; i++) {
            int flags = fcntl(fds[i], F_GETFD);
            if (flags < 0 || !(flags & FD_CLOEXEC)) fail(helper);
            count++;
        }
    }
    const char *engine = getenv("AGENT_CI_ENGINE_LOCK");
    if (count != (engine && *engine ? 3 : 2)) fail(helper);
    if (send(pair[0], "ACK1", 4, MSG_NOSIGNAL | MSG_DONTWAIT) != 4) fail(helper);
    close(pair[0]);
    /* Reap before managed startup: no detached guardian and no second owner. */
    for (;;) {
        int status;
        pid_t result = waitpid(helper, &status, WNOHANG);
        if (result == helper) {
            if (!WIFEXITED(status) || WEXITSTATUS(status)) fail(-1);
            return;
        }
        if (result < 0 && errno == ECHILD) fail(-1);
        if ((result < 0 && errno != EINTR) || millis() >= deadline) fail(helper);
        struct timespec pause = {.tv_nsec = 10000000};
        nanosleep(&pause, NULL);
    }
}
