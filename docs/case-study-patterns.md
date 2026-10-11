# What good district StoryMaps have in common

The first versions of storymap-gen passed every check and still produced thin stories: two sections, four slides, about 150 words. To find out what a good story looks like, the structure of
31 public student district StoryMaps (a university urban-systems course, listed on the [CDSpec site](https://cdspec.tech.cornell.edu/)) was measured: node types, headings, word counts, maps, pictures, links.
Only counts and structure were taken; no text was copied, and the students are not named here.

## The numbers

| measure | median (31 stories) | what it means |
|---|---|---|
| words of prose | 2,963 (range 801 to 8,832) | these are long-reads, not slide decks |
| main sections (h2) | 5 | a clear spine |
| sub-headings (h3) | 11, and 74% of stories also use a third level (h4) | every section is divided |
| maps per story | 7 (range 0 to 43); 318 of 320 maps have a caption | maps are placed beside the sentence they illustrate, and always captioned |
| pictures | 22; only 34% have a caption and 3 of 736 have alt text | pictures carry atmosphere; accessibility is mostly missing |
| paragraph length | 41 words (90% are 102 words or fewer; 2% exceed 150) | short paragraphs, many of them |
| links per story | 14 (19 of 31 stories have 5 or more) | claims point at sources inline |
| layouts | 84% use sidecar slides for part of the story; 77% docked, 36% floating | the narrative is long-form, with a few map-led slide sequences |

Elements used (share of stories): bullet lists 52%, separators 52%, picture galleries 55%, swipe maps 45%, quotes 29%, timelines 26%, charts 19%, key-number panels 10%, videos 19%, guided tours 16%.
Covers: a full-bleed picture 45%, a minimal cover 19%.

## Eight patterns, and what storymap-gen does with each

1. **A fixed spine.** Most stories follow one framework: introduction, hard systems (mobility, infrastructure, water, waste), soft systems (social and economic life, culture, parks, environment and resilience), interactions or an emergent pattern, and a proposal.
   *Used:* `init --framework systems` builds that spine; `essay` builds a thesis-driven variant.
2. **Every subsection says how the place supports a system, how it strains it, and what is unusual about it.** The best stories answer all three under each h3.
   *Used:* each generated subsection has a "finding" paragraph, a map and a "meaning" paragraph.
3. **The map sits beside the sentence it proves.** Maps are not collected at the end; they are interleaved, usually one per finding, and each has a caption.
   *Used:* `map` blocks inline in every layout; the gate warns about a map without a caption, and about a map that shows more than the text counts (`map.filters`).
4. **Analysis beyond counting.** The strongest passages measure a relationship ("a short buffer around the station reaches most of the district's bus routes"), not just a total.
   *Used:* the claim types `within_distance`, `area_coverage` (reach), `layer_density` and `layer_share` make such sentences checkable.
5. **Two layers side by side.** Nearly half the stories use a swipe to compare hard and soft systems.
   *Used:* `swipe` blocks (two sides of one generated map, with shared base layers).
6. **Short paragraphs, then a list or a number.** The median paragraph is 41 words; bullet lists summarise; some stories use large-number panels for the headline figures.
   *Used:* `bullets` and `figures` blocks; the depth report warns about paragraphs over 220 words.
7. **Sources inline.** Links sit in the sentence; most stories end with references.
   *Used:* `[label](https://...)` links in any text, per-paragraph `cites`, and a numbered source list generated from the data manifest.
8. **A thesis in the title and an honest closing.** Good titles name the idea ("the crossroads", "under pressure"), and good endings say what the evidence cannot show.
   *Used:* the writing guide asks for a thesis before any text and a "limits" subsection at the end; the gate cannot judge either, so the skill treats them as part of the brief.

## What the course stories do less well, and storymap-gen does differently
- **Numbers are not checked.** Many figures are typed by hand. storymap-gen refuses to build a story with a number that no claim backs.
- **Pictures have no alt text.** The image block requires it.
- **Maps are often more complex than the sentence.** Maps here show exactly the features the text counts.

## Limits of this study
31 stories from one course and one city; one reader's count of what is structure and what is content; no judgement of text quality beyond length and layout. The thresholds in the depth report
(`brief`, `standard`, `full`) are rounded from these medians and are a guide, not a rule.
