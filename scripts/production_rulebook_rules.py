"""Authored performance-director instructions, distinct from observed labels.

These are hypotheses to benchmark, not acoustic measurements or human verdicts.
No mood is forbidden merely because development evidence is sparse.
"""

# choose, do not choose, nearest contrast. Scene reasoning takes precedence over
# punctuation, mood prevalence, isolated keywords and automatic model agreement.
MOOD_RULES = {
    'neutral': ('Use ordinary speaking for factual statements, acknowledgements and uncomplicated exchange when the scene supports no marked stance.', 'Do not flatten an explicit threat, plea, emotional outburst or important change in intention; absence of a keyword is not absence of expression.', 'calm'),
    'calm': ('Use composed, softer and unhurried delivery when the speaker maintains an established relaxed register while describing events or discussing a problem.', 'Do not infer calm from a peaceful subject or a long sentence. A dangerous subject can be told calmly; an urgent performance needs a supported departure.', 'neutral'),
    'soft': ('Keep the voiced delivery down for intimacy, restraint or a deliberately subdued address supported by the relationship and scene.', 'Do not choose from sad subject matter alone, and do not convert quiet voiced speech into a whisper.', 'calm'),
    'whisper': ('Use confidential or concealed speech when the performer actually speaks privately, secretly or in an explicitly whispered manner.', 'The narrator saying that someone whispered does not make the attribution itself a whisper. A low-intensity performance need not be whispered.', 'soft'),
    'reflective': ('Reason through evidence, options or consequences; let the listener follow an inward thought process rather than announce a finished conclusion.', 'Do not label every first-person thought or question Thinking. Distinguish active investigation, confusion and an already settled commitment.', 'curious'),
    'reminiscent': ('Recount a past experience or search memory with attention on recalling rather than solving the present problem.', 'Past tense alone is ordinary narrative grammar. A historical fact or a memory can be stated normally without a remembering performance.', 'reflective'),
    'explaining': ('Teach or clarify a mechanism, procedure, rule or reason to a listener, organizing information so they understand.', 'Technical vocabulary alone is insufficient: a fact may be Normal, a considered explanation Calm, or a commitment Confident.', 'confident'),
    'solemn': ('Give a consequential decision, responsibility, ethical concern or formal address deliberate seriousness, with attention to its weight.', 'Do not manufacture ceremonial solemnity from titles or importance alone; do not assume Serious requires shouting or a particular pitch.', 'calm'),
    'warm': ('Address someone with welcome, familiarity, appreciation or companionable concern; the relationship is foregrounded.', 'Friendly words can be sarcastic or manipulative. Read who addresses whom and why; active easing of a specific worry is Comforting.', 'reassuring'),
    'gentle': ('Soften how a sensitive message lands through considerate, kind treatment of the listener.', 'Do not equate gentleness with low volume or female identity. Kindness, friendliness and quietness describe different choices.', 'warm'),
    'reassuring': ('Address an identifiable concern with trust, safety or a manageable way forward, intending to reduce the listener\'s distress.', 'Do not transfer reassurance to an author clause merely because it describes a comforting gesture; encouragement aims to make someone act.', 'encouraging'),
    'relieved': ('Show pressure easing after a preceding strain, danger, discomfort or uncertainty has passed.', 'A good outcome without prior tension may be Happy or Triumphant. Find the release in the preceding scene.', 'cheerful'),
    'cheerful': ('Express enjoyment, satisfaction or pleasant anticipation when positive feeling is the main stance.', 'Do not treat all increased energy as happiness. Alarmed involvement is Excited or Anxious even when emphatic.', 'excited'),
    'excited': ('Perform heightened emotional involvement in discovery, anticipation, admiration, celebration or agitated protest.', 'Excited is not necessarily positive or fast. Do not choose solely from an exclamation mark or an excited person mentioned by the narrator.', 'energetic'),
    'energetic': ('Give active forward momentum to a plan, decision, practical exchange or account of events while preserving its emotional stance.', 'Do not imply faster articulation automatically. Distinguish emotional activation, immediate pressure and rapid physical action.', 'excited'),
    'triumphant': ('Savour a completed achievement or victory, emphasizing earned success or a proudly demonstrated result.', 'A plan not yet achieved is usually Confident rather than Triumphant; pleasure alone can be Happy.', 'confident'),
    'playful': ('Invite shared amusement, teasing, mischief or light flirtation with an interlocutor or the reader.', 'Identify whether the joke is shared or cutting; a hostile target suggests sarcasm, mockery or contempt instead.', 'sarcastic'),
    'curious': ('Explore an unresolved fact with interested attention, ask to learn, notice an unusual detail or speculate openly.', 'A question can be rhetorical, worried, accusatory or confused. Classify the purpose of asking, not the question mark.', 'perplexed'),
    'confident': ('State a settled conclusion, feasible plan, commitment or assertion of competence with assurance.', 'An uncertain hypothesis, lesson, boundary or order has a different purpose. Do not infer certainty merely because a powerful character speaks.', 'firm'),
    'encouraging': ('Help another person continue or act by restoring motivation or making success seem achievable.', 'Removing a fear is Comforting; announcing success is Triumphant; ordering compliance is Commanding.', 'reassuring'),
    'awe': ('Dwell on the extraordinary scale or significance of a discovery with amazement.', 'A brief unexpected fact is Surprised. There need not be admiration when the discovery is frightening.', 'surprised'),
    'questioning': ('Use straightforward questioning when requesting an answer is the main act without a stronger interested, doubtful or emotional stance.', 'Do not use this as the default for all question marks; check curiosity, confusion, worry and rhetorical challenge first.', 'curious'),
    'perplexed': ('Express difficulty reconciling what happened with the speaker\'s understanding; something does not make sense.', 'An interested search for an answer is Curious; reluctance to commit is Hesitating; disbelief from impact can be Quiet shock.', 'curious'),
    'hesitant': ('Hold back, qualify a proposal, reconsider an action or approach an uncomfortable admission without committing fully.', 'An ellipsis or pause can also mark fatigue, shock or ordinary phrasing. Find the unresolved commitment.', 'reflective'),
    'worried': ('Attend to feared consequences, another person\'s welfare or what may have gone wrong, with concern rather than immediate loss of control.', 'A neutral calculation of risk is not automatically worry. Immediate pressure is Anxious; active reasoning can be Thinking.', 'anxious'),
    'anxious': ('Convey pressure felt now: a close threat, deadline, exposure or costly need to get something right.', 'Do not require fast tempo or high pitch. Distinguish composed danger assessment from tension and future concern.', 'worried'),
    'surprised': ('Register a sudden change of expectation or discovery of an unexpected fact.', 'A fact can surprise the character without the narrator performing surprise. Distinguish lasting impact, confusion and amazement.', 'shocked'),
    'shocked': ('React to a destabilizing, alarming or difficult-to-accept development with marked emotional impact.', 'Do not infer outward intensity solely from capitals or punctuation; immobilized disbelief may be Quiet shock.', 'stunned'),
    'stunned': ('Let a realization stop the performer: frozen disbelief, speechlessness or a moment of stunned recognition.', 'Quiet shock is a performance interpretation, not a rule that forbids emphatic punctuation. Distinguish fatigue and deliberate silence.', 'shocked'),
    'embarrassed': ('Express social discomfort about dignity, status, exposure or being diminished.', 'Protesting perceived unfairness is Offended; anger or a status claim is not necessarily embarrassment.', 'indignant'),
    'afraid': ('Express fear of a threatening person, presence or possibility when the fear itself is the dominant experience.', 'Anxiety about executing a task and concern about consequences need not be direct fear. Avoid amplifying every danger into panic.', 'anxious'),
    'panicked': ('Show loss of composure under threat: disorganized urgency, desperate repetition or inability to recover control.', 'Quoted panic belongs to the quoted part, not automatically the surrounding report. Do not equate urgency with loss of control.', 'afraid'),
    'sad': ('Express contained sorrow, loss or bleakness.', 'Deflated expectation is Disappointed; overwhelming anguish is Deeply sad; subdued delivery alone is Soft.', 'disappointed'),
    'disappointed': ('React to a hoped-for result failing or help being unavailable with deflated expectation.', 'Check whether the main act is grief, anger, criticism or practical explanation rather than disappointment.', 'sad'),
    'grieving': ('Express overwhelming anguish over a meaningful loss; the loss need not be a death.', 'Do not infer anguish from a tragic topic alone or amplify ordinary frustration into grief.', 'sad'),
    'distressed': ('Express emotional discomfort already being experienced and difficult to contain, combining pain, vulnerability or frustration.', 'Future concern is Worried; bodily suffering is Injured; a startled reaction is not necessarily sustained distress.', 'worried'),
    'injured': ('Convey physical suffering or inability to tolerate bodily discomfort, preserving the specific sensation and degree of strain.', 'A narrated injury does not make every line sound injured; exhaustion is Tired and emotional pain is Distressed.', 'tired'),
    'tired': ('Express depletion, reduced capacity or burdensome effort after exertion or strain.', 'Do not confuse comfortable calm, low volume and shock with exhaustion.', 'calm'),
    'fast_action': ('Carry rapidly unfolding physical events and immediate reactions through the narration, keeping each action legible.', 'A speaker asking for speed is Urgent; an engaged account of ordinary activity is Energetic. Pace must still be validated in rendering.', 'energetic'),
    'shouting': ('Use projected raised-voice delivery when vocal projection is the performance requirement.', 'Warning is a purpose, not automatically a shout. The word shouted in attribution does not change the author\'s own voice.', 'warning'),
    'angry': ('Confront with blame, resentment or hostile feeling as the main stance.', 'Strong certainty and authority can be calm. Distinguish friction, unfairness, threat and decisive refusal.', 'irritated'),
    'irritated': ('Make annoyance at an unwelcome complication, repeated problem or behaviour audible without presuming a particular intensity.', 'Do not claim Annoyed is acoustically quieter than Angry; the existing evidence does not establish that comparison.', 'angry'),
    'indignant': ('Protest something felt to be unfair, insulting or illegitimate, often defending a person or principle.', 'A personal discomfort can be Embarrassed; confrontation without the unfairness stance is Angry.', 'angry'),
    'threatening': ('Make an intended harmful consequence or intimidating menace felt, whether through speech or deliberate narrated menace.', 'Do not confuse reporting a threat, fearing it or warning someone about it with issuing one.', 'warning'),
    'suspicious': ('Question someone\'s credibility or motives, withholding trust while assessing their account.', 'Do not convert every uncertainty into distrust. Curiosity seeks information; sarcasm supplies a critical edge.', 'curious'),
    'sarcastic': ('Use pointed irony, dry critical commentary or a knowing discrepancy between literal words and intended meaning.', 'Do not apply from a joke word alone. Distinguish shared playfulness and direct contempt; consider the target and relationship.', 'playful'),
    'mocking': ('Make fun of a target through ridicule or imitation.', 'A shared joke is Playful; irony can be Sarcastic without ridiculing a person.', 'sarcastic'),
    'contemptuous': ('Dismiss another person\'s abilities, words or concerns from a position of superiority.', 'A speaker\'s powerful role or customary voice does not make all their lines contemptuous. Look for the actual dismissal.', 'sarcastic'),
    'disgusted': ('Recoil from something repulsive with aversion as the main reaction.', 'Social superiority is Contemptuous and hostile blame is Angry; a bad outcome alone is not disgust.', 'contemptuous'),
    'warning': ('Alert others to danger or a harmful consequence with the purpose of making them attend and respond.', 'A system readout with an exclamation mark can remain Normal. Distinguish an alert from a threat issued by the speaker.', 'threatening'),
    'urgent': ('Press for an immediate response because time matters, keeping the request\'s purpose clear.', 'Fast physical narration is Fast action; panic loses control; an ordinary command need not be urgent.', 'commanding'),
    'firm': ('Set a boundary, close negotiation or refuse to move from a decision.', 'Confidence expresses certainty; orders direct another person\'s action. Firm need not be angry or loud.', 'commanding'),
    'commanding': ('Direct others\' actions or demand compliance with an exercise of authority.', 'An imperative can be an invitation, plea, reassurance or practical suggestion. Identify the relationship and expected compliance.', 'firm'),
}

DIRECTOR_PROTOCOL = [
    'Resolve scene participants, speaker and presentation route before choosing delivery. Preserve fixed speech/narration boundaries and every internal part.',
    'Determine what this performer is doing to whom, what they know, what changed and what response they seek. Read the complete paragraph and scene, including subtext and attributions.',
    'Use the selected character and voice route\'s examples. Never substitute another character\'s performance as evidence for this character, even when their voice-family IDs match.',
    'Keep dialogue, first-person storytelling, author clauses, quoted thoughts, game messages, openings and written/telepathic presentation distinct. A character identity alone does not determine all voice routes.',
    'Apply explicit performance instructions to the relevant speech part, not mechanically to the narrator clause describing them. Inspect dashes, quotations, parenthetical asides and printed placeholders individually.',
    'Scene emotion is evidence, not a label: fear described in text can be told calmly; an instruction can be Friendly; irony can reverse the literal reading.',
    'Do not select a mood because of frequency, sentence length, isolated words, punctuation, gender or rank. Frequencies describe development evidence only.',
    'Calm versus Normal is a subtle register choice. Maintain a coherent established register across a scene; do not toggle it with sentence function alone. Human labels remain unchanged.',
    'A baseline is a preference under genuinely unmarked delivery, not an escape from uncertainty. Clear expressive evidence with unresolved interpretation requires critic/adjudication.',
    'Rare or absent development examples do not forbid a mood. Mark a strong-cue proposal as under-supported and request independent analysis; do not silently merge it into a neighbour.',
    'Keep the active scene stance until intention or circumstances change. Reset at a real turn or author transition when appropriate; do not spread one speaker\'s emotion into surrounding narration.',
    'Treat stored speaker identities as proposals unless their provenance says human. A reviewed voice or mood is not a speaker gold verdict.',
    'Cite rule IDs and exact source spans for every marked choice. State the nearest alternative and why it loses; confidence must later be calibrated by the sealed benchmark.',
    'An initial assignment, critic and background repair are separate recorded decisions. Provider agreement is supporting evidence, not an acceptance guarantee.',
    'Repair only affected upcoming units after validation. Do not change currently playing audio mid-unit or silently overwrite earlier plans; replace audio and timing atomically at a safe boundary.',
    'This book is an evaluation candidate. Mood assignment does not establish that Higgs controls can reproduce it or that any example is approved training audio.',
]

# Character applications are authored from inspected DEVELOPMENT examples. They
# specify what to examine, not a psychological trait asserted from frequency.
CHARACTER_DIRECTIVES = {
    'narrator': [
        'Distinguish the protagonist\'s immediate inner reaction from his retrospective storytelling voice and from his direct spoken dialogue.',
        'Keep observation and exposition composed where supported; use Curious for active discovery, Thinking for weighing options, Confused for a broken mental picture, and Surprised for the unexpected realization.',
        'Separate practical forward momentum from an emotional outburst and from the physical speed of a combat sequence. Tension can be deliberate rather than fast.',
        'Do not make an author attribution imitate the character\'s emotion automatically. Retain expression where the narrator himself laughs, protests or reacts.',
    ],
    'rb1_ivan': [
        'Read diplomatic explanation as an attempt to orient or persuade the listener. A considered account can stay Calm or Normal even when its subject is dangerous.',
        'Choose Confident when Ivan commits to a concrete plan or settled conclusion; Explaining when the point is to teach why; Serious when responsibility or consequences are foregrounded.',
        'Distinguish a reported adversary\'s threat from Ivan issuing a threat himself. Check whether the scene asks for narrated menace, worry or composed explanation.',
        'Use Sarcastic only for a pointed critical edge supported by the exchange; do not make his diplomatic competence a blanket confident or superior tone.',
    ],
    'rb1_kirill': [
        'Komar, Kirill and Kirill Komarov share identity. His dialogue must stay separate from the first-person narrator route.',
        'Separate settled plans and tactical commitments from technical explanation and firm boundaries. An imperative is not automatically an order.',
        'Read teasing greetings and provocative questions through the relationship: Friendly and Playful invite contact; Sarcastic challenges; Offended defends against unfair allegations.',
        'Requests for cooperation may Encourage or Comfort without asserting authority. Check the response he is seeking from this particular listener.',
    ],
    'rb1_uline': [
        'Distinguish matter-of-fact trade information, a patient explanation and confidential information about unofficial dealings.',
        'Technical or financial terminology does not force Explaining; development examples also place explanations in Normal and Calm.',
        'Use Whisper only with confidentiality or concealed speech evidence; use Soft for a subdued voiced exchange. Evaluate disappointing news separately from the mechanics she describes.',
        'Choose Friendly or Playful from her relationship to the listener, not from her species or the voice-family label.',
    ],
    'rb1_tamara': [
        'Separate legion leadership and practical consequences from an intimate personal conversation. A commander can speak calmly, warmly or softly.',
        'Concerns about others\' actions or consequences can be Worried; immediate pressure can be Anxious. A description of danger alone is insufficient.',
        'Inspect whether she is instructing, setting a boundary, defending fairness or asking for understanding before selecting a strong mood.',
    ],
    'rb1_irishka': [
        'In reports of other people\'s distress, separate her present stance from any quoted or imitated performance inside the report.',
        'Distinguish practical proposals from agitated objections: forward activity can be Energetic, while involvement in a disputed decision can be Excited without being happy.',
        'Use Curious for seeking information and Worried for anticipated consequences; a question alone does not distinguish them.',
    ],
    'rb1_kisly': [
        'Separate tactical instructions, ordinary squad information and companionable attention to recruits.',
        'Calls to get moving can carry Energetic momentum without anger or shouting. Friendly guidance can concern uncomfortable practical conditions.',
        'Serious focuses on responsibility; Remembering recounts an earlier battle or experience. Military content alone does not decide the mood.',
    ],
    'rb1_uraz': [
        'Separate calm ship-status reporting, confident navigation decisions and commands requiring crew compliance.',
        'When he explains detector limitations, focus on making the situation legible; when he allocates actions to crew, focus on authority and compliance.',
        'Do not convert captaincy into a permanent commanding mood. Confidence can be expressed through deliberate assessment of risk.',
    ],
    'rb1_tyulenev': [
        'Separate personnel assessment, personal disclosure and frustration over an operational problem.',
        'An evaluative criticism can remain Normal; a personal medical account can be Soft; an urgent capability gap may be Anxious.',
        'Use Serious for consequential responsibility rather than automatically for every official statement.',
    ],
    'rb1_minno': [
        'Keep her distinctive speech grammar as source text; do not mistake it for confusion, hesitation or low intelligence.',
        'Separate assertions of status, protests about treatment, questions seeking an offer and concern about her people.',
        'Written or mechanical presentation variants are distinct routes. Do not replace the direct voice merely because the mood changes.',
    ],
    'rb1_svetlana': [
        'Separate training instructions, friendly practical guidance, concern for a mission and personal vulnerability.',
        'An instruction to assemble can be Friendly or Energetic. Authority alone does not justify Commanding.',
        'Distinguish hesitating about prospects from refusing surrender on principle, which can be Offended.',
    ],
    'rb1_radugin': [
        'Separate official announcements, private consideration and a direct decision to dismiss or direct someone.',
        'A public threat alert is Warning when its purpose is to alert the faction; capitalization alone is insufficient.',
        'Multiple observed voice families need route resolution. Do not impose a single voice or infer a delivery merely from office or rank.',
    ],
    'rb1_kosta': [
        'Inspect a dismissive judgment, a question seeking information, an order dismissing people and an excited discovery separately.',
        'Looking-down delivery needs actual dismissal of the listener; it must not become his default merely because several examples use it.',
        'Keep a critical edge distinct from ordinary factual speech and from projected command.',
    ],
    'rb1_filira': [
        'Separate friendly recognition of Komar, an offer in a trade, an interested request and a consequential territorial concern.',
        'A confident offer asserts what she will provide; Friendly addresses the relationship; Curious seeks information.',
        'Alternate voice evidence remains a separate route requiring context; do not infer a new voice from the seriousness of her request.',
    ],
    'rb1_dmitry': [
        'Read practical assistance and flight reports separately from surprise at a discovery and worry about what others will conclude.',
        'Keep known voice-family differences visible as unresolved routing evidence until contextual explanation is established. A majority vote cannot settle them.',
        'Use Serious for a consequential assessment, Energetic for active forward planning, and Worried for feared consequences; technical language is not itself a mood.',
    ],
    'rb1_imran': [
        'Distinguish ordinary practical observation, interested invitations, active accounts of events and emotionally excited discoveries.',
        'An excited call to look at a discovery is different from a curious invitation; preserve the relationship and scene progression.',
    ],
    'book2:Лидия Вертячих': [
        'Read an amused or flirtatious invitation as a social act, distinct from factual identification or practical help.',
        'Mechanical or written presentation and direct dialogue require separate routes, even when the thoughts concern the same relationship.',
        'Playful must be shared amusement rather than inferred from gender, occupation or personal proximity.',
    ],
    'book2:Айни Ури-Миайуу': [
        'Separate accusation over perceived unfairness, requests for help, explanation of a relationship and energetic offers to act.',
        'A plea to be taken away can be Excited or Distressed rather than Friendly. Resolve the urgency and relationship from the surrounding exchange.',
        'The short-name Ayni identity is not resolved by voice-family similarity. Use an explicit cast alias or request identity adjudication.',
    ],
    'book2:Морф (Фокси)': [
        'Separate curious clarification of a plan, ordinary conversation, a subdued account and worry when a situation turns against the group.',
        'Species or disguise does not establish an alias or voice transformation rule by itself. Foxi/Morph naming is an alias candidate until the cast resolver corroborates it.',
    ],
    'book2:Фокси': [
        'Separate curious clarification of a plan, ordinary conversation, a subdued account and worry when the situation turns against the group; inspect changes of intention rather than mood frequency.',
        'When cast evidence establishes Foxi/Morph as one identity, share its identity policy while keeping each presentation voice separate. Disguising herself as Ayni does not merge her with the real translator.',
    ],
    'book2:Амиру У-Маяу': [
        'Inspect whether she is delivering a consequential assessment, teaching, reacting with concern or participating in an active exchange.',
        'The differing observed voices and mostly proposed speaker identities make route resolution important. Do not treat every religious or formal passage as her voice or as Serious.',
    ],
    'book2:Рикки Пан-Миис': [
        'Separate practical bargaining, equipment limitations, annoyance at difficulties and openly hostile reaction.',
        'Keep a composed assessment or ordinary negotiation distinct from irritation; material disadvantage alone does not force anger.',
    ],
    'book2:Глава прайда Ловкой Лапы': [
        'Distinguish an ordinary wager condition, a settled outcome, a dismissive objection, a question about an unfamiliar ability and an actual threat.',
        'Status does not imply contempt. Make menace depend on the intended harmful consequence, not the voice timbre.',
    ],
    'book2:Тини': [
        'Separate acknowledgment, curious discovery, excited benefit and a destabilizing physical surprise.',
        'Do not use a childish or high voice as evidence of excitement. The possible longer-name alias needs cast corroboration.',
    ],
    'book2:Тини Уи-Комар': [
        'Keep ordinary replies, disappointment and excitement separate, with sparse evidence clearly marked.',
        'The short-name Tini alias is a candidate; do not combine the evidence until identity is independently corroborated.',
    ],
    'book2:Эдуард Бойко': [
        'Distinguish composed introductions and commitments from confusion, disappointment and worried questioning.',
        'A consequential promise to become a companion can be Serious without an outburst. Conflicting voice evidence needs route or attribution investigation.',
    ],
    'game_system': [
        'Render explicit system readouts as readouts, including levels, rewards and skill changes. Exclamation marks are not evidence of performed excitement.',
        'Narration about an interface and a character quoting its message remain separate structural parts. Confirm the system channel before selecting the mechanical voice.',
    ],
}

GENERIC_CHARACTER_DIRECTIVES = [
    'For this character, establish their present relationship to the listener, practical objective and change of intention from the development examples and current scene.',
    'Use this character\'s observed voice route and local baseline only where delivery is genuinely unmarked. A role title, rank or species does not determine personality or mood.',
    'Sparse development evidence is a limitation, not a prohibition. Do not invent a characteristic manner or borrow another character\'s performance; request more context or adjudication when needed.',
]
