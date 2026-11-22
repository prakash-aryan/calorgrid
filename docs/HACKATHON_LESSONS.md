# Basel GreenTech Hackathon — Post-Event Retrospective & Lessons Learned

## Context
At the Sprintd Basel GreenTech Edition, Team Invincibles built **CalorGrid** with deep scientific and engineering rigor: a conserved 8,760-hour physics engine (`<= 7.3e-9 kWh` hourly residual), five SHA-256 pinned datasets, 5-year Swiss gas meter validation (`0.14%` error), three scheduling/siting engines (`HeatShift` EDF, 689k-job Alibaba GPU trace, 520-H100 alpine look-ahead fleet, and 100 CH × 100 NZ siting), live POSIX `SIGSTOP`/`SIGCONT` workers, 352 automated tests, and a live 4-tab 3D Swiss digital twin.

Although we did not place in the Top 3, observing the qualifying and winning teams surfaced a crystal-clear strategic lesson for future hackathons and pitch competitions.

## Key Lesson Learned: Showmanship + Commercial Story First, Verification Second
1. **Lead with visual showmanship and business upside, not verification spreadsheets:**
   - Qualifying teams led with **high-impact 3D visual storytelling** and clear **business / revenue / margin narratives**, rather than spending precious stage minutes defending validation residuals, sensitivity failure boundaries, and dataset provenance.
   - Scientific verification belongs in the GitHub repository, technical appendix, and judge Q&A—not competing for attention on the main 5-minute pitch slides.
2. **Unify the 3D presentation and the 3D product into ONE seamless experience:**
   - Switching between flat slide decks and a separate web application breaks stage momentum.
   - Winning presentations stayed inside **one cohesive 3D visual flow**—using smooth 3D camera transitions to explain the problem, demonstrate how the 3D platform works right inside the transition, and highlight the money engine (`CHF` saved, cloud margin, scalable annual profit) with minimal on-screen text.

## Immediate Next-Iteration Upgrade Built From This Lesson
- **Unified Interactive 3D Story & Embedded 3D Platform Deck:**
  - Live HTML link: https://calorgrid-deck.pages.dev/3d/
  - Repository copy: [`docs/pitch3d/index.html`](pitch3d/index.html)
  - Six continuous Three.js camera-flight acts with zero tab-switching:
    1. **The Paradox:** 3D hyperscale monolith venting waste heat vs. cold Swiss chalets.
    2. **Inside the Micro-Cloud:** Smooth 3D dive into glass-cutaway liquid-cooled racks, plate CDU, thermal buffer, and glowing radiant coils (`CHF 0` host cost).
    3. **Live 3D Swiss Alps Network:** Sculpted `swisstopo` 3D relief map with live **Winter ↔ Summer** toggle rerouting optical fiber arcs in 2 ms (`0.0 kWh` wasted).
    4. **Interactive 3D Platform Inside the Slide:** Live 3D host morph (`Basel Home` / `St. Jakob Pool` / `St. Gallen Hospital`) + live dispatch tier switcher (`Spot −60%` / `On-Demand` / `Fiber Shift`) explaining how the platform works without leaving the presentation.
    5. **The Money Engine:** Interactive 3D cloud-pricing & scalable golden net-profit skyline (`65 → 500 → 2,000 servers`, scaling `CHF 1.25M → CHF 38.5M/yr` net profit at `20%` margin).
    6. **Anti-Phase Global Scale & Close:** Rotating 3D orbital globe bridging Switzerland ↔ New Zealand with Team Invincibles & scannable QR codes.
