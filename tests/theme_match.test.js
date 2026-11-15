const fs=require("fs");
const vm=require("vm");
const context={window:{},console};
context.globalThis=context;
vm.createContext(context);
vm.runInContext(fs.readFileSync("assets/theme-match.js","utf8"),context);
const themeMatches=context.window.themeMatches;
const repeatingThemes=context.window.repeatingThemes;
const defs=context.window.THEME_DEFS;
function theme(name){return defs.filter(function(item){return item.name===name;})[0];}
function assert(condition,message){if(!condition)throw new Error(message);}
const rel=theme("Отношения");
const anxiety=theme("Тревога");
const esteem=theme("Самооценка");

assert(!themeMatches({situation:"Жду ответа клиента",thought:"Отправил предложение и жду ответа"},rel),"client reply must not count as relationships");
assert(!themeMatches({thought:"Нет ответа уже второй день",situation:"Переписка с заказчиком"},rel),"a bare reply must not count as relationships");
assert(!themeMatches({thought:"Это зона моей ответственности"},rel),"ответственность must not count as relationships");
assert(themeMatches({thought:"Он молчит, и я читаю это как конец отношений"},rel),"silence in a relationship counts");
assert(themeMatches({situation:"Поссорились с партнером",thought:"Снова ревность"},rel),"a quarrel and jealousy count");
assert(themeMatches({emotion:"Тревога",emotions:["Тревога"],situation:"Жду ответа клиента",thought:"Просто жду"},anxiety),"selected anxiety emotion counts");
assert(!themeMatches({emotion:"Тревога",emotions:["Тревога"],thought:"Жду ответа клиента"},rel),"anxiety emotion does not count as relationships");
assert(themeMatches({thought:"Меня накрыла тревога и беспокойство"},anxiety),"anxiety stems count");
assert(themeMatches({thought:"Все лучше меня, я ничтожество"},esteem),"self-esteem phrase counts");
assert(themeMatches({emotions:["Стыд"],thought:"На совещании молчал про проект"},esteem),"shame counts as self-esteem");
assert(!themeMatches({emotions:["Стыд"],thought:"На совещании молчал про проект"},rel),"shame alone is not a relationship");

const few=repeatingThemes([
 {emotions:["Тревога"],thought:"Первая"},
 {emotions:["Тревога"],thought:"Вторая"}
]);
assert(!few.ready,"two entries are not enough for the themes block");
const many=repeatingThemes([
 {emotions:["Тревога"],thought:"Первая"},
 {emotions:["Тревога"],thought:"Вторая"},
 {situation:"Жду ответа клиента",thought:"Третья про работу"}
]);
assert(many.ready,"three entries with anxiety twice unlock the block");
assert(many.themes.some(function(item){return item.name==="Тревога"&&item.count===2;}),"anxiety count");
assert(!many.themes.some(function(item){return item.name==="Отношения";}),"client reply stays out of the carousel");
const none=repeatingThemes([
 {situation:"Жду ответа клиента",thought:"Один"},
 {situation:"Счёт за услуги",thought:"Два"},
 {situation:"Дедлайн проекта",thought:"Три"}
]);
assert(!none.ready,"unrelated entries do not invent a theme");
console.log("theme matching checks ok");
