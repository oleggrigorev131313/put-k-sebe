(function(root){
"use strict";
function normalizeRu(value){
 return String(value==null?"":value).toLowerCase().replace(/ё/g,"е");
}
function words(value){
 return normalizeRu(value).match(/[a-zа-я0-9]+/g)||[];
}
function hasStem(text, stem){
 stem=normalizeRu(stem);
 if(!stem)return false;
 var source=words(text);
 if(stem.indexOf(" ")>=0){
  var parts=words(stem);
  if(!parts.length||source.length<parts.length)return false;
  for(var i=0;i<=source.length-parts.length;i++){
   var ok=true;
   for(var j=0;j<parts.length;j++){
    if(source[i+j]!==parts[j]&&source[i+j].indexOf(parts[j])!==0){ok=false;break;}
   }
   if(ok)return true;
  }
  return false;
 }
 for(var n=0;n<source.length;n++){
  if(source[n]===stem||source[n].indexOf(stem)===0)return true;
 }
 return false;
}
var THEME_DEFS=[
 {name:"Тревога",glyph:"wave",stems:["тревог","страх","страшн","беспоко","паник","напряж"],emotions:["Тревога","Страх","Беспокойство","Напряжение","Паника","Неуверенность","Опасение"]},
 {name:"Самооценка",glyph:"sun",stems:["самооцен","ничтож","неудач","лучше меня","хуже меня","недостаточно хорош","сравнива","сравнение","виноват"],emotions:["Стыд","Вина","Смущение","Неловкость","Унижение","Чувство неполноценности"]},
 {name:"Отношения",glyph:"nodes",stems:["отношен","молчит","молчат","молчани","промолч","ревност","ревну","расста","поссори","ссор","партнер","бросил","близост"],emotions:["Ревность","Отверженность"]}
];
function entryEmotions(entry){
 var list=[];
 if(entry&&Array.isArray(entry.emotions))entry.emotions.forEach(function(item){if(item)list.push(String(item));});
 if(!list.length&&entry&&entry.emotion)String(entry.emotion).split(",").forEach(function(item){var text=item.trim();if(text)list.push(text);});
 return list;
}
function entryText(entry){
 if(!entry)return "";
 return [entry.thought,entry.situation,entry.balanced,entry.forText,entry.against,entry.action].filter(Boolean).join(" ");
}
function themeMatches(entry, theme){
 if(!entry||!theme)return false;
 var text=entryText(entry);
 if((theme.stems||[]).some(function(stem){return hasStem(text,stem);}))return true;
 var selected=entryEmotions(entry).map(normalizeRu);
 return (theme.emotions||[]).some(function(name){
  var needle=normalizeRu(name);
  return selected.some(function(item){return item===needle||item.indexOf(needle)===0;});
 });
}
function repeatingThemes(entries){
 var list=(entries||[]).slice();
 var themes=THEME_DEFS.map(function(theme){
  return {name:theme.name,glyph:theme.glyph,count:list.filter(function(entry){return themeMatches(entry,theme);}).length};
 }).filter(function(theme){return theme.count>0;});
 return {ready:list.length>=3&&themes.some(function(theme){return theme.count>=2;}),themes:themes};
}
root.THEME_DEFS=THEME_DEFS;
root.themeMatches=themeMatches;
root.repeatingThemes=repeatingThemes;
root.entryEmotions=entryEmotions;
})(typeof window!=="undefined"?window:globalThis);
