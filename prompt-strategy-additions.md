Add two proven fantasy football strategy concepts to the decision engine. Both use data 
we already have — no new scraping or data sources needed.

## 1. Bye Week Lookahead

The scraped DataFrame already has Week 1 through Week 18 columns per player. A bye week 
shows up as a 0 (or null) projection for that player sandwiched between normal weeks. 
Right now nothing in the tool looks ahead at this.

Add a function `check_upcoming_byes(my_roster, current_week, lookahead_weeks=2)` in 
decision_engine.py that:
- For each player on my current roster, checks their projection for the next 
  `lookahead_weeks` weeks (e.g. if current week is 3, check weeks 4 and 5)
- Flags any player with a 0/null projection in that window as an upcoming bye
- Cross-references against starting lineup slots — a bye hitting a BENCH player is low 
  priority info, but a bye hitting a current STARTER is a real heads-up that should be 
  surfaced clearly, since it means that slot needs a replacement plan soon
- Returns a simple structured warning list, similar in shape to the existing 
  `BenchDepthWarning` dataclass, e.g. `ByeWeekWarning(player, position, bye_week, 
  is_current_starter)`

Add a new "Upcoming Byes" section to both the terminal and markdown reports (report.py), 
shown BEFORE the Action Plan section since it's forward-looking context, not a this-week 
action. Format: "⚠️ Michael Pittman Jr. (WR1, starter) has a bye in Week 5 — plan a 
replacement before then." Only show players within the lookahead window; don't clutter 
the report with byes 10 weeks out.

## 2. Position-Differentiated Streaming Thresholds

Right now config.yaml has a single global `min_vor_gain_to_recommend_add` threshold used 
for every position. This doesn't reflect a well-established real distinction: QB/TE/K/DEF 
are legitimate to swap weekly for a modest points edge (low switching cost, matchup-driven, 
proven strategy), while RB/WR should require a MUCH higher bar to justify a swap, since 
their value is tied to held role/opportunity and short-term projection swings are less 
reliable signal for those positions.

1. Replace the single `min_vor_gain_to_recommend_add` value in config.yaml with a 
   per-position dict, e.g.:
   ```yaml
   min_vor_gain_to_recommend_add:
     QB: 2.0
     TE: 2.0
     K: 1.0
     DEF: 1.0
     RB: 8.0
     WR: 8.0
   ```
   Keep a sensible default (e.g. 5.0) as a fallback for any position not explicitly listed, 
   so nothing breaks if a position is missing from config.

2. Update wherever `min_vor_gain_to_recommend_add` is currently read (in 
   `recommend_adds_drops` or wherever the threshold gets applied) to look up the 
   position-specific value instead of a single global one.

3. Update the recommendation reasoning text to make this visible — e.g. "Add Harrison 
   Mevis (K) — VOR gain +2.0 clears the streaming threshold (1.0) for K" vs. "Add Rachaad 
   White (RB) — VOR gain +10.8 clears the higher bar (8.0) required for RB, since RB value 
   depends on held role, not weekly streaming."

Add tests confirming:
- A QB/TE/K/DEF add with a small VOR gain (e.g. +2.0) IS recommended under the new 
  position-specific threshold
- An RB/WR add with that SAME small VOR gain (+2.0) is NOT recommended, correctly filtered 
  out under the higher RB/WR bar
- The bye week check correctly identifies a 0-projection week within the lookahead window 
  and correctly distinguishes starter vs. bench byes

Run the full test suite after both changes and show me the results, plus show me a real 
sample report section for each new feature using my actual roster data.

Update AGENTS.md's "Design principles already established" section to document both of 
these once implemented, so future sessions don't rediscover or accidentally revert them.
