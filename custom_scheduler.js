// print the existing states
const log = false;
if (log) console.log(JSON.stringify(states, null, 4));
if (log) console.log(JSON.stringify(customData, null, 4));

// Custom Scheduler v1.0.0


const deckParams = [
    {
        // Default parameters of Custom Scheduler for global
        "deckName": "global config for Custom Scheduler",
        // Base interval that is multiplied by ease factor to get the max interval
        // at which the interval growth is at the minimum
        // For example, a card at 250% ease, that interval is 500 days
        "daysUpper": 200,
    },
    {
        // Example 1: User's custom parameters for this deck and its sub-decks.
        "deckName": "MainDeck1",
        "daysUpper": 200,
    },
    // Example 2: User's custom parameters for this deck and its sub-decks.
    // Don't omit any keys.
    {
        "deckName": "MainDeck2::SubDeck::SubSubDeck",
        "daysUpper": 100,
    }
];

// To turn off FSRS in specific decks, fill them into the skip_decks list below.
// Please don't remove it even if you don't need it.
const skipDecks = [];

// Custom Scheduler supports displaying memory states of cards.
// Enable it for debugging if you encounter something wrong.
const displaySchedulerState = false;

// display if Custom Scheduler is enabled
if (displaySchedulerState) {
    const prev = document.getElementById('scheduler_status');
    if (prev) {
        prev.remove();
    }
    var schedulerStatus = document.createElement('span');
    schedulerStatus.innerHTML = "<br>Custom scheduler enabled";
    schedulerStatus.id = "scheduler_status";
    schedulerStatus.style.cssText = "font-size:12px;opacity:0.5;font-family:monospace;text-align:left;line-height:1em;";
    document.body.appendChild(schedulerStatus);
    document.getElementById("qa").style.cssText += "min-height:50vh;";
}

let currentDeckParams = {};
// get the name of the card's deck
let deckName = getDeckname();
if (deckName) {
    // Arrange the deckParams of sub-decks in front of their parent decks.
    deckParams.sort(function (a, b) {
        return -a.deckName.localeCompare(b.deckName);
    });
    for (let i = 0; i < deckParams.length; i++) {
        if (deckName.startsWith(deckParams[i]["deckName"])) {
            currentDeckParams = deckParams[i];
            break;
        }
    }
} else {
    if (displaySchedulerState) {
        schedulerStatus.innerHTML += "<br>Deck name not found";
    }
}
if (Object.keys(currentDeckParams).length === 0) {
    currentDeckParams = deckParams.find(deck => deck.deckName === "global config for Custom Scheduler");
}

// Write customData to DOM so we can read it in the template with javascript, if we want
// And also handle setting cached content into alt_element_id elements
var customDataDiv = document.getElementById("customData");
if (!customDataDiv) {
    customDataDiv = document.createElement("div");
    customDataDiv.id = "customData";
    var currentCustomData = JSON.parse(states.current.customData);
    Object.entries(currentCustomData).forEach(([key, value]) => {
        customDataDiv.dataset[key] = value;
    });
    document.body.appendChild(customDataDiv);
}

// Set the customData values to 'review' for all buttons so that rescheduling will be applied
customData.again.v = 'review';
customData.hard.v = 'review';
customData.good.v = 'review';
customData.easy.v = 'review';
// Also set cache value to zero so that new cached values are to be created
customData.again.fc = 0;
customData.hard.fc = 0;
customData.good.fc = 0;
customData.easy.fc = 0;

// Don't adjust intervals for new, learning cards
if (states.current.normal?.new
    || (states.current.normal?.learning)
    || states.current.filtered?.rescheduling.originalState.new
    || states.current.filtered?.rescheduling.originalState.learning) return;

// Do adjust for reviews
const revObj = states.current.normal?.review
    || states.current.normal?.relearning?.review
    || states.current.filtered?.rescheduling?.originalState?.review
    || states.current.filtered?.rescheduling?.originalState?.relearning.review

const curFct = revObj?.easeFactor;
const curRevIvl = revObj?.scheduledDays;

const {
    daysUpper: daysUpper,
} = currentDeckParams;
const minModFct = Math.sqrt(curFct);
const adjDaysUpper = daysUpper * curFct;

const {
    scheduledDays: hardIvl,
} = states.hard.normal?.review || {}
const {
    scheduledDays: goodIvl,
} = states.good.normal?.review || {}
const {
    scheduledDays: easyIvl,
} = states.easy.normal?.review || {}
const hardIvlMult = Number.isFinite(hardIvl) && hardIvl / curRevIvl;
const easyIvlMult = Number.isFinite(easyIvl) && Number.isFinite(goodIvl) && easyIvl / goodIvl;

if (log) console.log('adjDaysUpper', adjDaysUpper)
if (log) console.log('curFct', curFct);
if (log) console.log('minModFct', minModFct);
if (log) console.log('curRevIvl', curRevIvl)
if (log) console.log('hardIvlMult', hardIvlMult);
if (log) console.log('easyIvlMult', easyIvlMult);


function getDeckname() {
    return ctx?.deckName
        || document.getElementById("deck")?.getAttribute("deckName");
}

function adjustIvl(answerIvl, mult) {
    const ratio = Math.min(answerIvl / adjDaysUpper, 1);
    const minModMult = Math.sqrt(mult);
    const modMult = Math.min(mult, mult * (1 - ratio) + minModMult * ratio);
    const modIvl = Math.min(answerIvl, curRevIvl * modMult);
    if (log) console.log('ratio', ratio);
    if (log) console.log('minModMult', minModMult);
    if (log) console.log('modMult', modMult)
    if (log) console.log('modIvl', modIvl);
    return Math.ceil(modIvl);
}

if (curRevIvl) {
    let goodModIvl;
    if (goodIvl) {
        goodModIvl = adjustIvl(goodIvl, curFct);
        if (log) console.log('mod good', goodIvl, goodModIvl);
        if (states.good.normal?.review) {
            states.good.normal.review.scheduledDays = goodModIvl;
        }
    }

    if (hardIvl && hardIvlMult && hardIvlMult >= 1 && goodIvl) {
        const hardGoodRatio = Math.min(hardIvl / goodIvl, 1);
        // Try to keep a significant difference between hard and good ivls even when factor may be low
        // Return a ivl between the unchanged hardIvl and curIvl that's closes to curIvl the closer
        // hardIvl is to goodIvl
        const modHardIvl = Math.ceil(hardIvl * (1 - hardGoodRatio) + curRevIvl * hardGoodRatio)
        if (log) console.log('hardGoodRatio', hardGoodRatio);
        if (log) console.log('mod hard', hardIvl, modHardIvl);
        if (states.hard.normal?.review) {
            states.hard.normal.review.scheduledDays = modHardIvl;
        }
    }

    if (goodModIvl && easyIvlMult) {
        const modEasyIvl = Math.ceil(goodModIvl * easyIvlMult);
        if (log) console.log('mod easy', easyIvl, modEasyIvl);
        if (states.easy.normal?.review) {
            states.easy.normal.review.scheduledDays = modEasyIvl;
        }
    }
}