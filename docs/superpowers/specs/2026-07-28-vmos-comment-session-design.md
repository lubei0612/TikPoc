# VMOS Brand Comment Session Design

## Objective

Replace ordinary bulk profile-trace work with a smaller brand-comment workflow
that combines relevant TikTok browsing with reviewed first-level comments. Six
named IKUN BAGS employee accounts each publish at most 20 comments per day while
remaining independent, observable, and recoverable after ordinary UI
interruptions.

The first production milestone is correctness and stable operation. Comment
reach, profile visits, follows, inbound messages, and qualified leads are
measured separately; no comment is treated as successful merely because a tap
or text-input command returned successfully.

## Confirmed Operating Rules

- One VMOS device maps to one named IKUN BAGS employee account.
- Each account may publish at most 20 first-level comments per local calendar
  day.
- One account may publish at most one comment on a given TikTok video.
- Normal production assignment prefers different videos across accounts. An
  explicitly marked experiment may assign the same video to multiple accounts,
  but every account still receives a distinct comment plan and never replies to
  another IKUN BAGS account merely to amplify the thread.
- Comments are English. Every plan stores a Chinese translation for operator
  review, but the translation is never submitted to TikTok.
- Comments contain no direct profile invitation, shop invitation, contact
  request, price, inventory promise, or private-channel destination.
- Emoji use is part of the immutable plan. Default distribution is zero or one
  emoji; the generator learns emoji choice and placement from the target
  video's high-engagement comments instead of appending a fixed symbol.
- Automatic follow-back and automatic direct messages remain outside this
  workflow.

## Account Personas

The shared brand prefix identifies the relationship while the suffix and
comment style remain account-specific. Example personas include:

- `IKUN BAGS | ZOEY`: styling, color, and trend observations.
- `IKUN BAGS | RAY`: leather, hardware, construction, and durability.
- `IKUN BAGS | MIA`: vintage, rarity, and collection context.
- `IKUN BAGS | LEO`: capacity, commuting, and purchase-use trade-offs.
- Remaining accounts receive similarly explicit humor or buyer-viewpoint
  personas.

The server owns persona configuration. The device APK receives only the final
immutable comment plan and does not generate or reinterpret copy locally.

## System Boundary

### Discovery and comment-learning plane

The existing `followers` collector accepts a TikTok video URL or video ID and
returns comment text, likes, reply count, creation time, language, and commenter
identity. A new planner consumes the collected comments, removes unusable
records, and summarizes the video's strongest discussion structures, such as
humor, disagreement, aesthetic judgment, professional detail, or personal
experience.

The planner produces several original candidates. An approved candidate becomes
an immutable plan containing:

- video ID and canonical URL;
- account and persona ID;
- English comment and Chinese translation;
- source-analysis digest;
- emoji decision;
- planned operating window;
- idempotency key and attempt state.

The system learns structure and topic rather than copying a source comment.

### Server scheduling plane

The server enforces the daily quota, video-account uniqueness, leases,
idempotency, and durable state. The initial daily shape is four ordinary content
sessions of approximately five comment tasks per account. The session boundary
is an operational grouping, not a requirement to submit five consecutive
comments.

Tasks transition monotonically through:

`planned -> leased -> video_verified -> submitted -> visible_confirmed`

or one of:

`skipped`, `verification_required`, `uncertain`, `failed`.

An `uncertain` submission is not immediately submitted again. Reconciliation is
read-only and checks for the already-visible comment before any operator creates
a replacement plan.

### Device session plane

The TikPoc APK independently pulls tasks over HTTPS, stores its checkpoint, and
uploads results. ADB remains limited to installation, upgrades, diagnostics,
and live acceptance.

The device session state machine is:

```text
idle
  -> relevant_browse
  -> comment_due
  -> video_opening
  -> video_verified
  -> comment_submitting
  -> comment_reconciling
  -> relevant_browse
```

Relevant browsing returns to TikTok's visible Home/Recommended surface and
performs bounded read-only browsing. It does not insert random follows and does
not require a like merely to make the session look active.

The APK verifies the exact target video before opening the comment composer. A
comment is complete only when the expected account's submitted text is visible
on the target video after the composer closes.

## VMOS Automation Findings

Live inspection of VMOS Automation `com.vmos.vmosauto` version `1.0.63` showed:

- an Accessibility Service and persistent foreground task service;
- a boot receiver and cloud WebSocket task channel;
- a generic RPA parser supporting click, coordinate tap, scroll, wait, loop,
  random-probability branches, HTTP requests, screenshots, and JavaScript;
- several concurrent UI interruptions during the enhanced TikTok maintenance
  template: friend-discovery permission, long-press menu, and image verification.

TikPoc will reuse the architectural ideas of a foreground service, cloud task
delivery, checkpoints, and semantic UI control. It will not depend on the VMOS
template parser or copy its task implementation. TikPoc owns a smaller typed
state machine with explicit evidence and failure states.

## Interruption and Recovery Policy

Before browsing, navigation, or comment submission, the APK classifies the
visible surface.

### Ordinary interruption

Friend-discovery prompts, ordinary dialogs, share sheets, and accidental
long-press menus are ordinary interruptions. The APK closes only a uniquely
identified dismiss or back action, returns to Home/Recommended, and verifies the
expected surface before continuing from the checkpoint.

### Verification interruption

Visible phrases such as `请完成下列验证后继续` or `Verify to continue`, together
with the challenge surface, produce `verification_required`.

On detection the APK:

1. stops all gestures, navigation, and comment submission;
2. preserves the current task and idempotency state;
3. records timestamp, account, device, surface digest, and a redacted screenshot;
4. reports the device as requiring operator attention;
5. keeps other devices independent and running.

The challenge widget is handled by the operator. The observed left-bottom reset
action may be performed manually twice when appropriate. After operator
confirmation, the APK performs a clean recovery:

1. return to the Android home surface;
2. stop the current TikTok activity without clearing application data;
3. relaunch TikTok;
4. require a stable Home/Recommended surface;
5. resume read-only browsing first;
6. return to the preserved comment task only in a later clean session.

If the challenge remains after relaunch, the account stays
`verification_required`; no automatic reset loop is created.

## Evidence and Metrics

Every comment attempt records:

- device and account IDs;
- persona and plan IDs;
- video ID and source-analysis digest;
- planned, leased, submitted, and reconciled timestamps;
- exact-video verification result;
- visible-comment confirmation or explicit error code;
- interruption and recovery events;
- comment likes and replies at configured observation points when available;
- profile visits, follows, inbound messages, and qualified leads as separate
  funnel events.

Report measured results after 2 and 24 hours. Do not infer reach or conversion
from the number of planned or submitted tasks.

## Delivery Stages

1. **Pure interruption semantics:** classify normal feed, ordinary dialog,
   long-press menu, and verification-required fixtures.
2. **Home recovery:** add a bounded typed recovery command with visible-state
   evidence and no data clearing.
3. **Durable comment plans:** add quota, uniqueness, leases, idempotency, and
   read-only reconciliation to the server and device store.
4. **First-level comment UI:** verify exact video, submit one immutable comment,
   and confirm its visible state.
5. **Followers integration:** import comment-analysis output and create reviewed
   employee-persona plans.
6. **Single-device live gate:** one VMOS account, then a five-comment session,
   then one full 20-comment day.
7. **Six-device promotion:** expand only after the single-device gate has no
   duplicate submissions and all visible-state checks pass.

## Acceptance Gates

- Unit tests cover every interruption classification and recovery transition.
- Recovery performs no likes, follows, messages, comments, or application-data
  clearing.
- Verification-required state blocks all comment submission until operator
  confirmation and a clean Home/Recommended check.
- One account never publishes two comments to the same video from the same plan
  or retry path.
- Daily account quota never exceeds 20 visible-confirmed plus unresolved
  submissions.
- A live five-comment canary confirms the exact video and visible comment for
  every successful task.
- A measured one-account day reports completion, uncertainty, verification,
  comment engagement, and funnel outcomes before six-device promotion.

