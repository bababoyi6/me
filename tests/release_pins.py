"""Explicit official release fixtures; historical recipes never follow a mutable latest."""
RELEASES={
    '3.5.16':('e6f46b2f3becf29bfd555cec8a059e429f7c082c1af402499b3a7940cf8ebd34',
        '1954f18faeb9f5838e9444880f2ffb00704c6bd5b2836bc86dd64611537d49b8','telemt 3.5.16'),
    '3.5.17':('0570762ddcd79530d666b09e955b955bb52c9e134cc8b8ed1a0f50ad28d58a0e',
        'e8ff277bbb8ec4676d6264e593fe2a0d255facec8f1cb305e357a3859198ffd4','telemt 3.5.17'),
}
NATIVE_BASES={'84e5b827a6619fb2e09ad3d09f253cdffe04bc2f':'3.5.16',
              '66708d2866f3f3a4541c4fa67984d408e71a8af4':'3.5.17'}

TLS_BASELINE = (
    'b4fec3efd10288a9f211ad958337fa314a15486e72fd5c50ab870f22a19737e5',
    '5dc5d9b5ac43a0f12a402159e127b3b69e18ccb355c99b0322e73d870d907100',
    'telemt 3.5.5+tdh.balanced2',
)
TLS_CANDIDATES = {
    'old': ('bulk', 'source_commit', 'ac71d92ec41dea00a7eafd6b8d350c3486633500',
            'telemt 3.5.5+tdh.balanced2.security1'),
    'new': ('balanced', 'upstream_commit', 'b594ee55270798fe21f23b721b50eab46c2e0bec',
            'telemt 3.5.17+tdh.main.b594ee5.balanced1'),
}
