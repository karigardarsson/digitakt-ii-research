CC      ?= cc
CFLAGS  ?= -O2
# Warnings are kept out of CFLAGS so a user-supplied CFLAGS cannot silently
# drop them. The code needs C11 for _Static_assert.
WARN     = -Wall -Wextra
STD      = -std=c11
SRCS     = $(wildcard *.c)
OBJS     = $(SRCS:.c=.o)

elektron-firmware-tool: $(OBJS)
	$(CC) $(STD) $(CFLAGS) $(WARN) -o $@ $(OBJS)

%.o: %.c
	$(CC) $(STD) $(CFLAGS) $(WARN) -c -o $@ $<

$(OBJS): $(wildcard *.h)

clean:
	rm -f elektron-firmware-tool $(OBJS)

.PHONY: clean
