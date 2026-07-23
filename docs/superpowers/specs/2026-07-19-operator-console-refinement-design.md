# Operator Console Refinement Design

## Direction

The Chinese console is a dense, restrained operations surface for repeated
monitoring. It keeps the existing charcoal, white, and cyan identity but removes
floating statistics, oversized empty regions, and sticky elements that cover
table rows.

## Operations Layout

The first viewport contains the top navigation, one compact KPI strip, the
command bar, and at least one complete device row. KPIs show round state,
coverage, device health, final-attempt mean/P90, and projected 20-hour capacity.

Below devices, a responsive two-column workband places rolling-hour pacing on
the wider left side and the coverage account on the right. The coverage account
is an ordinary aligned panel, never a floating overlay. Below 1100 px the two
regions stack. Target coverage and runtime evidence follow without decorative
section cards.

## Rolling Quota Table

For every device/outcome show used/limit, confirmed, uncertain, remaining,
token state, next due time, and current candidate weight. Labels say `滚动一小时`
and never claim an on-the-hour reset. Ready actions use a restrained green
indicator; waiting actions show the localized relative time.

## Responsive And Accessibility

Table headers remain within their own scroll containers and cannot obscure the
first row. Controls have stable heights and wrap Chinese text. Desktop
`1440x1000` and `1920x1080`, mobile `390x844`, and a full-page desktop screenshot
must have no overlap, clipping, horizontal viewport overflow, or blank sections.

