The repository release operator has identified `ops/release/release.env` as an
RM-2 release manifest. Normalize exactly that file into repository-root
`release.json`. Preserve the named source and every pre-existing repository
file byte-for-byte. Create no other file and do not use the network.
