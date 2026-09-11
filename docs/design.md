# How bisim computes an address

This page explains the address format, what goes into it, and the rules that keep it stable. Read it before changing anything in `bisim/probes.py`, `bisim/canon.py`, `bisim/_runner.py`, or `bisim/address.py`.

## The address

```
address = "bsm1:" + sha256("bisim/1" | probegen | sig_hash | root)
```

- `probegen` is the version of the input generator, currently `v1`.
- `sig_hash` is a hash of the parameter types and the return type. Parameter names and default values are not included. Renaming a parameter does not change the address; changing a default value changes behavior on the inputs that leave that parameter out, so it shows up as a behavior change, not a signature change. For a class, it covers the constructor types and the name, kind, parameter types and return type of every public method.
- `root` is the root of a Merkle tree over the (input, result) pairs. The leaves are `sha256(input_id + result_hash)`, sorted, so the order in which inputs were run does not matter.

## Identity and evidence

The address covers exactly the first 48 generated inputs (`STANDARD_COUNT`). That set depends only on the
signature and the generator version, so the address is a property of the code alone. It does not change
when someone adds a witness, when `reach` adds a suggested input, or when `diff` grows the probe set.

Witnessed and suggested inputs are hashed separately into the evidence value (`bse1:` plus 64 hex
characters). The manifest carries both. Verdicts (`diff`, `check`, `merge-check`) use every input that was
run; only the address is restricted to the standard set.

## Inputs

There are three kinds of inputs, called probes.

- **type**: generated from the type hints by a seeded generator. Edge cases come first, then seeded random values. The seed comes from `sig_hash`, so the same signature gives the same inputs everywhere.
- **witness**: an input a person approved, with the expected result. Stored in `.bisim/witness/`.
- **suggested**: an input a model proposed (from `mint` or `reach`) that reached code the generated inputs missed. Stored in the same file, but not approved by anyone.

A probe's id is the hash of its arguments in canonical form. If the same input appears in more than one kind, the strongest kind wins: witness, then suggested, then type.

For a method target the arguments are `(constructor_args, ((method, args),))`. For a class target they are `(constructor_args, ((method1, args1), (method2, args2), ...))` with one to four calls.

## Results

Each input is run in a child process. The result record is one of:

- `{"ok": true, "value": ...}` for a normal return
- `{"ok": false, "exc": "ValueError"}` for an exception (the type only, not the message)
- `{"timeout": true}` when the call exceeded the time limit

Optional fields:

- `out`: anything the call printed
- `effects`: files opened, files left in the scratch directory (with content hashes), environment variables read, and blocked network or subprocess attempts. Present only when the list is not empty.
- for class and method targets: `steps` (one record per call) and `state` (the public attributes of the object afterwards). A failing constructor gives `{"ok": false, "exc": ..., "at": "init"}`.

Before every input, the child process re-imports the target module and any project-local modules it pulled in, so module-level state starts fresh each time. Third-party and standard library modules stay loaded. The blockers for network, subprocess, file and environment access are installed before the first import, so import-time effects are recorded and blocked like any other.

Every input is run twice, in two separate processes, and the second process runs the inputs in reverse order. If the two results differ for any input, the target is refused and no address is produced. This catches randomness and it catches results that depend on state shared between calls.

`BISIM_ISOLATION=process` runs one child process per input. `BISIM_RUNNER_WRAPPER` prefixes the child command with a sandboxing tool for code you do not trust.

## Canonical form

Values are converted to a small tagged tree before hashing so that the same value hashes the same way everywhere:

| value | form |
|---|---|
| None | `["n"]` |
| bool | `["B", 0 or 1]` |
| int | `["i", "decimal string"]` |
| float | `["f", repr]` with `nan`, `inf`, `-inf` and `-0.0` handled explicitly |
| str | `["s", text]` |
| bytes | `["b", hex]` |
| list, tuple | `["l", [...]]`, `["t", [...]]` |
| set, frozenset | `["S", sorted items]` |
| dict | `["d", pairs sorted by key]` |
| dataclass | `["D", class name, fields sorted by name]` |
| other object | `["o", class name, public attributes sorted by name]` |
| anything else | `["r", type name, repr]`, marked opaque |

Opaque values are allowed but flagged, because `repr` output may differ between machines.

## Coverage

While running the inputs, bisim records which lines of the target executed and which line-to-line transitions happened. From that it reports:

- line coverage: executable lines of the target that ran at least once
- branch coverage: for each `if`, `elif`, `while` and `for`, whether the true branch and the false branch were each taken at least once

Coverage is reported alongside the address. It is not part of the hash.

`diff` and `check` double the number of generated inputs (48, 96, 192, up to 480). They stop early once a behavioral difference is found. Otherwise they keep going while something is still unreached, or while the two versions differ in text (a rewrite with no difference found yet is exactly when more inputs are worth their cost).

## Rules for changing the code

1. Any change to how inputs are generated must change `PROBEGEN_VERSION`. Old addresses must not silently collide with new ones.
2. Any change to result records or canonical form must keep the fixture tests green: every refactor pair keeps its address, every mutant pair changes it.
3. Adding a new field to a result record must not change the result for code that does not use the feature. This is why `effects` is only present when non-empty.
4. Never produce an address for a target that could not be run deterministically. Refuse and say why.
5. Every "changed" verdict must come with a concrete input and both results.

## Version history of the format

- 1.0: functions, methods and classes; effects; line and branch coverage; structural form for objects.
- 1.1: the address covers the 48 standard inputs only; witnesses and suggested inputs moved to the separate evidence hash. Inputs that leave out defaulted parameters were added to the generator. Module state is reset between inputs. Addresses of functions without defaults and without module-level state are unchanged from 1.0. Addresses of functions with default parameters changed, because the generated input set changed. Addresses of targets that had witnesses changed, because witnesses no longer feed the address.
