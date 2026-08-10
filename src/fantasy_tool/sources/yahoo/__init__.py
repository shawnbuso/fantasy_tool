"""Pulling a real league's history out of Yahoo.

Split deliberately into three pieces:

`auth` gets a session, once, by hand. Yahoo's login is defended by 2FA and device
checks, which is exactly the part not worth automating.

`fetch` walks the league's pages and caches every response to disk. It knows nothing
about what the pages contain.

`parse` turns cached pages into a League. It never touches the network.

The split matters because the API application is expected to come through eventually.
When it does, only `fetch` is replaced -- the cache format, the parser, the ID
crosswalk and every test around them survive unchanged.
"""
