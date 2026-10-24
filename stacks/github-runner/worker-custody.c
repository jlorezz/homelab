#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <pthread.h>
#include <poll.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stddef.h>
#include <time.h>
#include <limits.h>

/* The receiver returns without closing these process-owned CLOEXEC descriptors.
 * There is no helper, fork, managed-runtime callback, or long constructor wait.
 * Actual .NET descriptor survival and auto-update remain required live gates.
 */
static int leases[3];
static int expected;
static int listener;

static void fail(void) {
    static const char message[] = "CI Worker kernel admission failed\n";
    (void)!write(STDERR_FILENO, message, sizeof(message) - 1);
    /* Also closes every received FD, including malformed/truncated transfers. */
    _exit(125);
}

static long long millis(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) fail();
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static int descendant(pid_t pid) {
    for (int depth = 0; depth < 128 && pid > 1; depth++) {
        char path[64], stat[4096];
        snprintf(path, sizeof(path), "/proc/%ld/stat", (long)pid);
        int fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0) return 0;
        ssize_t n = read(fd, stat, sizeof(stat) - 1);
        close(fd);
        if (n <= 0) return 0;
        stat[n] = '\0';
        char *end = strrchr(stat, ')');
        char state;
        long parent;
        if (!end || sscanf(end + 1, " %c %ld", &state, &parent) != 2 || parent <= 0) return 0;
        if (parent == (long)getpid()) return 1;
        pid = (pid_t)parent;
    }
    return 0;
}

static void *receive_leases(void *unused) {
    (void)unused;
    int channel;
    do { channel = accept4(listener, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK); }
    while (channel < 0 && errno == EINTR);
    if (channel < 0) fail();
    close(listener);
    struct ucred peer;
    socklen_t length = sizeof(peer);
    if (getsockopt(channel, SOL_SOCKET, SO_PEERCRED, &peer, &length) ||
        length != sizeof(peer) || peer.uid != getuid() || !descendant(peer.pid)) fail();
    /* Only a connected transfer has a short deadline. Admission queueing happens
     * in the started hook, after managed Worker IPC startup, before connecting. */
    long long deadline = millis() + 2000;
    struct pollfd event = {.fd = channel, .events = POLLIN};
    int ready;
    do {
        long long left = deadline - millis();
        if (left <= 0) fail();
        ready = poll(&event, 1, (int)left);
    } while (ready < 0 && errno == EINTR);
    if (ready <= 0 || !(event.revents & POLLIN)) fail();
    char payload[5];
    union { struct cmsghdr align; char bytes[CMSG_SPACE(8 * sizeof(int))]; } control;
    struct iovec iov = {.iov_base = payload, .iov_len = sizeof(payload)};
    struct msghdr message = {.msg_iov = &iov, .msg_iovlen = 1,
                            .msg_control = control.bytes, .msg_controllen = sizeof(control.bytes)};
    ssize_t received = recvmsg(channel, &message, MSG_CMSG_CLOEXEC | MSG_DONTWAIT);
    if (received != 4 || message.msg_controllen > sizeof(control.bytes) ||
        memcmp(payload, "CIC1", 4) || (message.msg_flags & (MSG_CTRUNC | MSG_TRUNC))) fail();
    int count = 0;
    for (struct cmsghdr *cmsg = CMSG_FIRSTHDR(&message); cmsg;
         cmsg = CMSG_NXTHDR(&message, cmsg)) {
        if (cmsg->cmsg_len < CMSG_LEN(0) ||
            (char *)cmsg + cmsg->cmsg_len > control.bytes + message.msg_controllen ||
            cmsg->cmsg_level != SOL_SOCKET || cmsg->cmsg_type != SCM_RIGHTS ||
            (cmsg->cmsg_len - CMSG_LEN(0)) % sizeof(int)) fail();
        size_t n = (cmsg->cmsg_len - CMSG_LEN(0)) / sizeof(int);
        int *fds = (int *)CMSG_DATA(cmsg);
        for (size_t i = 0; i < n; i++) {
            struct stat stat;
            int flags = fcntl(fds[i], F_GETFD);
            if (count >= expected || flags < 0 || !(flags & FD_CLOEXEC) ||
                fstat(fds[i], &stat) || !S_ISREG(stat.st_mode)) fail();
            leases[count++] = fds[i];
        }
    }
    if (count != expected) fail();
    if (send(channel, "ACK1", 4, MSG_NOSIGNAL | MSG_DONTWAIT) != 4) fail();
    close(channel);
    return NULL;
}

__attribute__((constructor)) static void prepare_worker(void) {
    char exe[PATH_MAX];
    ssize_t size = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    if (size < 0 || (size_t)size >= sizeof(exe) - 1) fail();
    exe[size] = '\0';
    const char *base = strrchr(exe, '/');
    const char *profile = getenv("AGENT_CI_PROFILE");
    if (!base || strcmp(base + 1, "Runner.Worker") || !profile || !*profile) return;
    const char *engine = getenv("AGENT_CI_ENGINE_LOCK");
    expected = engine && *engine ? 3 : 2;
    unsigned char nonce[16];
    size_t filled = 0;
    while (filled < sizeof(nonce)) {
        ssize_t n = getrandom(nonce + filled, sizeof(nonce) - filled, GRND_NONBLOCK);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) fail();
        filled += (size_t)n;
    }
    char name[80];
    int prefix = snprintf(name, sizeof(name), "ci-custody-%ld-", (long)getpid());
    if (prefix < 0 || prefix + 32 >= (int)sizeof(name)) fail();
    for (int i = 0; i < 16; i++) snprintf(name + prefix + i * 2, 3, "%02x", nonce[i]);
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    memcpy(address.sun_path + 1, name, strlen(name));
    listener = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
    if (listener < 0 || bind(listener, (struct sockaddr *)&address,
        (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + strlen(name))) ||
        listen(listener, 1) || setenv("AGENT_CI_CUSTODY_ENDPOINT", name, 1)) fail();
    pthread_attr_t attr;
    pthread_t thread;
    if (pthread_attr_init(&attr) || pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED) ||
        pthread_create(&thread, &attr, receive_leases, NULL)) fail();
    pthread_attr_destroy(&attr);
    /* Return immediately: the Listener can send the complete JobRequest before
     * the started hook queues for up to 1800 seconds. CHANNEL_TIMEOUT is unchanged. */
}
