// print the existing states
const log = false;
if (log) console.log(JSON.stringify(states, null, 4));

// Don't adjust intervals for new, learning or relearning cards
if (states.current.normal?.new 
  || states.current.normal?.learning
  || states.current.normal?.relearning
  || states.current.filtered?.rescheduling.originalState.new
  || states.current.filtered?.rescheduling.originalState.learning
  || states.current.filtered?.rescheduling.originalState.relearning) return;

// Do adjust for reviews
const revObj = states.current.normal?.review
  // || states.current.normal?.relearning?.review
  || states.current.filtered?.rescheduling?.originalState?.review
// || states.current.filtered?.rescheduling?.originalState?.relearning.review

const curFct = revObj?.easeFactor;
const curRevIvl = revObj?.scheduledDays;

const daysUpper = 225;
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
  let goodMult;
  if (goodIvl) {
    goodModIvl = adjustIvl(goodIvl, curFct);
    goodMult = goodModIvl / curRevIvl;
    if (log) console.log('mod good', goodIvl, goodModIvl);
    if (states.good.normal?.review) {
      states.good.normal.review.scheduledDays = goodModIvl;
    }
  }

  if (hardIvl && hardIvlMult && hardIvlMult >= 1) {
    let modHardIvl = adjustIvl(hardIvl, hardIvlMult);
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