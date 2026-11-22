# Embedded storage options for message history shared by two containers

Research for JLYH-6 (parent map: JLYH-5, web UI). Facts and trade-offs only. The storage decision
belongs to the storage grilling ticket.

Question: what fits recording message history written by the worker container and read by a
separate UI process, in a plain Docker or Unraid deployment?

Statements in quotes come from the cited page. Items marked **Inference** are reasoning from those
quotes, not a statement by the source.

## 1. SQLite across processes

### Locking and concurrency

- Default (rollback journal) locking: "Only one process at a time can hold a RESERVED lock. But
  other processes can continue to read the database while the RESERVED lock is held." A writer that
  cannot get the lock "fails and returns SQLITE_BUSY." Source: https://www.sqlite.org/lockingv3.html
- Locking relies on the OS: "SQLite uses POSIX advisory locks to implement locking on Unix ...
  SQLite assumes that these system calls all work as advertised." Same source.
- Without WAL, a database can have "multiple simultaneous readers or one writer, but not
  simultaneous readers and writers." Source: https://www.sqlite.org/useovernet.html

### WAL mode

- "writers and readers can run at the same time", but "there can only be one writer at a time."
  Source: https://www.sqlite.org/wal.html (section 2.2)
- WAL is persistent: "after being set it stays in effect across multiple database connections and
  after closing and reopening the database." Source: https://www.sqlite.org/pragma.html
- Same-host requirement: "All processes using a database must be on the same host computer; WAL
  does not work over a network filesystem. This is because WAL requires all processes to share a
  small amount of memory." Source: wal.html (section 1)
- The shared memory is a file: "The wal-index is implemented using an ordinary file that is mmapped
  for robustness", created "in the same directory as the database itself." Source: wal.html
  (section 7)
- Read-only readers are constrained. Opening a WAL database needs write access to the `-shm` file
  or to the containing directory. Since 3.22.0 a read-only open works if `-shm` and `-wal` already
  exist or can be created, or the database is immutable. Source: wal.html (section 1)
- Reader/checkpoint interaction: "a long-running read transaction can prevent a checkpointer from
  making progress", and with always-overlapping readers "the WAL file will grow without bound."
  Default auto-checkpoint is at 1000 pages (about 4 MB). Sources: wal.html (sections 2.2, 6);
  pragma.html (`wal_autocheckpoint`)
- Durability: with `synchronous=NORMAL`, "A transaction committed in WAL mode ... might roll back
  following a power loss or system crash." `FULL` is durable in WAL mode. Source: pragma.html
- Files in play: the main database plus `-wal` and `-shm` siblings. "If two or more processes open
  the database using different names, then they will use different rollback journals and WAL
  files." Source: https://www.sqlite.org/howtocorrupt.html (section 2.6)

### Network and bind-mounted filesystems

- "SQLite is designed for situations where the data and application coexist on the same machine."
  Network filesystem locking "has been known to operate incorrectly for some network filesystems.
  This has led to database corruption." Source: useovernet.html
- "POSIX advisory locking is known to be buggy or even unimplemented on many NFS implementations."
  "Your best defense is to not use SQLite for files on a network filesystem." Source: lockingv3.html
- useovernet.html lists three options for remote access: a client/server database; WAL with all
  reads and writes from processes on the machine holding the file (behind a proxy); or rollback mode.
- The SQLite pages fetched do not mention Docker, containers or mount namespaces. None address
  bind mounts specifically.
- Docker: "A given volume can be mounted into multiple containers simultaneously." Named volumes
  live "within a directory on the Docker host", and the page says nothing about concurrent writes or
  file locking by applications. Source: https://docs.docker.com/engine/storage/volumes/
- **Inference:** two containers on one host share one kernel, so a local named volume or a bind
  mount of a local disk is the same-host case WAL describes. A volume or bind mount that is itself
  backed by a network share (NFS, SMB) falls under the network filesystem warnings. The sources
  above do not test this; it would need verification on the target deployments (including Unraid
  share types).

### Escape hatches (with documented risk)

- `mode=ro` opens read-only (URI filenames must be enabled). `immutable=1` "skips all file locking
  and change detection", and if the file changes anyway, "SQLite might return incorrect query
  results and/or SQLITE_CORRUPT errors." `nolock=1` disables locking and can cause corruption if
  any other connection writes. Source: https://www.sqlite.org/uri.html
- `PRAGMA query_only` makes a connection reject writes with `SQLITE_READONLY`. Source: pragma.html

## 2. Python standard library

- `sqlite3` is in the standard library, but the docs call it "an optional module" and say it
  requires the third-party SQLite library, so availability depends on how Python was built.
  Source: https://docs.python.org/3/library/sqlite3.html
- `connect()` waits 5 seconds by default for a lock before raising `OperationalError`. Same source.
  SQLite also has `PRAGMA busy_timeout`. Source: pragma.html
- Connections are bound to their creating thread by default (`check_same_thread=True`). With
  `False`, "write operations may need to be serialized by the user to avoid data corruption." Same
  Python docs source.
- Transaction handling: `autocommit` defaults to `LEGACY_TRANSACTION_CONTROL`, which "will change to
  `False` in a future Python release"; `False` is described as the recommended value. Same source.
- No new dependency is needed to use SQLite from Python. The repo currently depends on `pika` in both
  packages and `flask` in the API (`api/pyproject.toml`, `workers/pyproject.toml`); neither is a
  database library. Other storage libraries (ORMs, other embedded databases) would be new
  dependencies and were not evaluated.

## 3. No shared file: RabbitMQ-based alternatives

Repo context (`docs/development/worker-rules.md`): delivery is at-least-once; the worker acks after
the job finishes; retry and dead-letter copies are published through the default exchange; the work
queue must stay declared `durable=True` with no arguments.

- A queue holds messages in states "ready for delivery" and "delivered but not yet acknowledged."
  The queues page does not describe retention after consumption. Source:
  https://www.rabbitmq.com/docs/queues
- Streams are the broker's retained-history structure: they "model an append-only log of messages
  that can be repeatedly read until they expire", with "non-destructive consumer semantics",
  multiple readers, and a consumer-chosen start offset (`first`, `last`, `next`, numeric, timestamp).
  Retention is set by `max-age` and `max-length-bytes`. Streams must be durable (never exclusive),
  are declared with `x-queue-type=stream` by the client, require per-consumer prefetch, and are
  best used via the stream protocol plugin ("highly recommended"). Source:
  https://www.rabbitmq.com/docs/streams
- Trade-offs (**Inference** except where cited):
  - Outcome events published by the worker to a stream would let the UI process read history without
    any shared file, so there is no cross-container locking concern.
  - History then lives in the broker. Querying (filter by status, search, pagination by arbitrary
    field) is not a stream feature as described; the UI process would have to consume and build its
    own view, which needs its own storage or an in-memory rebuild.
  - Retention is time or size based, not row based.
  - It adds a new durable broker-side resource and new publisher permissions; the repo notes a
    restricted RabbitMQ user needs write access to `amq.default` for existing publishes.
  - Publishing an outcome event is a second publish alongside the ack. At-least-once semantics mean
    a duplicate event is possible, so a UI-side consumer would have to tolerate repeats.
  - The stream plugin and protocol requirements were not checked against the RabbitMQ version
    deployed here.

## 4. Summary of trade-offs

| Option | Shared file | New dependency | Concurrent read/write | Main documented risk |
|---|---|---|---|---|
| SQLite, WAL, local volume | Yes (db, `-wal`, `-shm`) | No (stdlib) | Readers plus one writer | Must stay same host and local filesystem; read-only readers need `-shm`/directory access |
| SQLite, rollback mode | Yes | No | Readers or one writer | `SQLITE_BUSY` waits; locking relies on OS |
| SQLite on network-backed share | Yes | No | Documented as unreliable | Lock bugs, corruption |
| RabbitMQ stream of outcome events | No | No (pika already used; stream plugin/protocol not evaluated) | Non-destructive multi-reader | No query layer; time/size retention; broker config |
| Client/server database | No | Yes, plus a new container | Server-managed | Not evaluated beyond SQLite's own recommendation |

## Open questions not answered by primary sources

- Behavior of the WAL `-shm` mmap across two containers on Docker Desktop (macOS) bind mounts, which
  the repo's dev stack uses. The fetched sources do not cover it.
- Unraid share types and their locking behavior.
