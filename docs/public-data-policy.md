# What a station shows the public

> **Canonical for:** what may leave a sigmond station for public view, which
> way it may travel, who decides, and what it may cost.  Every public-facing
> piece (live views, public station pages, the docs site) follows this page.

Draft for review, 2026-11-25.  Michael Hauan and Rob Robinett.

A sigmond station records for science first.  Some of what it measures
also makes sense to people outside the project: a live waterfall, the
Doppler shift on a time station, the magnetometer's reading this second.
Showing it costs little and invites interest.  It also opens a door, and
this page says how wide.

## The rules

**1. Data goes out.  Nothing from the public comes in.**  A station opens
every connection it makes to the public side; nobody on the internet can
open one to the station.  A public viewer never reaches radiod, a client,
a shell or a configuration file.

One narrow exception: a viewer may choose among feeds the station already
offers, such as which ham band's waterfall to watch.  The choice travels
as a name from the station's own list, and the station ignores any name
not on it.  A viewer can never send a frequency, a bandwidth, a gain or
any other parameter.  Choosing a feed costs the station nothing it has not
already agreed to spend (rule 4).

**2. The operator decides, per station and per feed.**  Every public feed
starts off.  The operator turns each one on in the site profile, and
sigmond carries that choice out.  Turning a feed off stops it within a
minute.  Fleet administrators may set limits that bind every station, and
may turn a feed off fleet-wide; they never turn one on at a station
without its operator.

**3. Every feed carries the station's own identity.**  A station signs
what it sends with the key it already uses for its uploads, made at
install and registered with the fleet.  The public side learns a
station's name from that key, never from what the station claims.
Withdrawing a station's registration stops its uploads and its public
feeds together.

**4. Sigmond sets the budget, and recording comes first.**  Each feed runs
inside a CPU and memory limit that sigmond sets, as it does for every
client.  When the station runs short, public feeds stop before any
recording or timing work slows.  A station's cost never grows with the
number of viewers: the relay copies a feed to each viewer, and the
station sends it once.

**5. Only derived data, never the raw stream.**  Public feeds carry
products: spectra a row at a time, Doppler traces, magnetometer readings,
spots.  Raw IQ, audio, logs, configuration, keys and passwords stay home.

**6. Location: the grid square, never the precise position.**  Public views
place a station by its six-character grid square.  The precise latitude
and longitude serve timing and stay private.

**7. The public side keeps minutes, not archives.**  A relay holds a short
backlog in memory so a new viewer sees a full screen, and writes nothing
to disk.  Data the project keeps travels the upload paths it already
uses.

## Three tiers

| Tier | What | Status |
|---|---|---|
| 0 | **Public, read-only**: live views, public station pages, the docs site | in scope now |
| 1 | **Local, interactive**: ka9q-web, a club's contest dashboard on the station's own network | allowed on the local network only; never public |
| 2 | **Public, interactive**: a WebSDR-like service where a listener tunes | future; out of scope |

Tier 2 would need more than these rules allow, and it gets its own
review when it comes.  Today's designs leave room for it: per-viewer cost
limits inside the station's budget, relay sessions as well as streams,
and tunnels only the station opens.

## Before something new goes public

Any new public feed, page or service answers four questions, in its
design and in review:

1. What crosses the boundary, and which way does it travel?
2. Who turns it on, and how does it stop?
3. What does it cost the station, and what stops it when the station runs
   short?
4. What could a hostile viewer do with it?

## Where this applies today

- **Live views** (`live-publisher`, design in its repository): rules 1-7.
  Band choice uses rule 1's exception.
- **Live CODAR and HFDL maps**, **the fleet board**: read from the
  fleet's database through a public account that can only read; stations
  take no part.  Rules 5 and 6.
- **Public station pages** (planned): rules 2, 5 and 6.
