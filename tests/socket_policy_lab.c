/* Linux-only causal experiment; NEVER preload into a production service.
 * Headerless so a private TinyCC compiler suffices on a disposable host.
 * Only exact 256 KiB TCP socket requests change; all other calls pass through.
 * The official binary, cryptography and authentication remain unchanged.
 */
extern void *dlsym(void *, const char *);
extern void *dlopen(const char *, int);
extern int dlclose(void *);
extern char *getenv(const char *);
extern int getsockopt(int, int, int, void *, unsigned int *);
extern int getsockname(int, void *, unsigned int *);
extern int strcmp(const char *, const char *);
extern long strtol(const char *, char **, int);
extern void *memcpy(void *, const void *, unsigned long);

typedef int (*setopt_fn)(int, int, int, const void *, unsigned int);

int setsockopt(int fd, int level, int option, const void *value, unsigned int length) {
    setopt_fn original = (setopt_fn)dlsym((void *)-1L, "setsockopt");
    const char *mode = getenv("TDH_LAB_SOCKET_MODE");
    int type = 0, protocol = 0, requested;
    unsigned int size = sizeof(int);
    /* ctypes' local dlopen may place this object after libc, unlike preload. */
    if (!original) {
        void *libc = dlopen("libc.so.6", 1 | 4); /* LAZY | NOLOAD */
        if (libc) {
            original = (setopt_fn)dlsym(libc, "setsockopt");
            dlclose(libc);
        }
    }
    if (!original) return -1;
    if (level != 1 || (option != 7 && option != 8) || length != sizeof(int) || !value || !mode)
        return original(fd, level, option, value, length);
    requested = *(const int *)value;
    if (requested != 262144 || getsockopt(fd, 1, 3, &type, &size) || type != 1)
        return original(fd, level, option, value, length);
    size = sizeof(int);
    if (getsockopt(fd, 1, 38, &protocol, &size) || protocol != 6)
        return original(fd, level, option, value, length);
    if (!strcmp(mode,"auto-lowat")) {
        int lowat=65536; /* TCP_NOTSENT_LOWAT: bound unsent data, keep TCP autotuning. */
        return original(fd,6,25,&lowat,sizeof(lowat));
    }
    if (mode[0] == 'a' && mode[1] == 'u' && mode[2] == 't' && mode[3] == 'o' && !mode[4])
        return 0; /* Preserve the kernel's TCP automatic buffer selection. */
    /* Side-specific counterfactuals use the known private client listener.
     * Outgoing calls can occur before connect/bind, so peer address is unusable.
     * Unknown/missing listener identity leaves the original policy intact. */
    if (!strcmp(mode, "client-auto") || !strcmp(mode, "dc-auto")) {
        const char *port_text = getenv("TDH_LAB_CLIENT_PORT");
        char *end = 0;
        unsigned char address[128] = {0};
        unsigned short family = 0;
        unsigned int address_size = sizeof(address);
        long port = port_text ? strtol(port_text, &end, 10) : 0;
        if (port > 0 && port <= 65535 && end && !*end &&
            !getsockname(fd, address, &address_size) && address_size >= 4) {
            memcpy(&family, address, sizeof(family));
            int client = ((unsigned int)address[2] * 256 + address[3]) == (unsigned int)port;
            if ((family == 2 || family == 10) &&
                ((!strcmp(mode, "client-auto") && client) || (!strcmp(mode, "dc-auto") && !client)))
                return 0;
        }
    }
    if (mode[0] == '1' && mode[1] == 'm' && !mode[2]) requested = 1048576;
    if (mode[0] == '4' && mode[1] == 'm' && !mode[2]) requested = 4194304;
    return original(fd, level, option, &requested, sizeof(requested));
}
