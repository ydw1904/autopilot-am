COUNTRIES = [
    "ae","af","ag","ai","al","am","ao","ar","as","at","au","aw","az",
    "ba","bb","bd","be","bf","bg","bh","bi","bj","bm","bn","bo","br","bs","bt","bw","by","bz",
    "ca","cd","cf","cg","ch","ci","ck","cl","cm","cn","co","cr","cu","cv","cy","cz",
    "de","dj","dk","dm","do","dz",
    "ec","ee","eg","er","es","et",
    "fi","fj","fm","fr",
    "ga","gb","gd","ge","gf","gh","gi","gl","gm","gn","gp","gq","gr","gt","gu","gw","gy",
    "hk","hn","hr","ht","hu",
    "id","ie","il","in","iq","ir","is","it",
    "jm","jo","jp",
    "ke","kg","kh","ki","km","kn","kr","kw","ky","kz",
    "la","lb","lc","lk","lr","ls","lt","lu","lv","ly",
    "ma","md","me","mg","mk","ml","mm","mn","mp","mq","mr","mt","mu","mv","mw","mx","my","mz",
    "na","nc","ne","nf","ng","ni","nl","no","np","nr","nu","nz",
    "om",
    "pa","pe","pf","pg","ph","pk","pl","pm","pr","pt","py",
    "qa",
    "re","ro","rs","ru","rw",
    "sa","sb","sc","sd","se","sg","sh","si","sk","sl","sn","so","sv","sy","sz",
    "tc","td","tg","th","tj","tl","tm","tn","to","tr","tt","tv","tw","tz",
    "ua","ug","us","uy","uz",
    "vc","ve","vn","vu",
    "wf","ws",
    "xk",
    "ye","yt",
    "za","zm","zw",
]

EXTRACT_JS = r"""(() => {
  var cards = document.querySelectorAll('.hubListBox');
  var lines = [];
  for(var i=1;i<cards.length;i++){
    var c = cards[i];
    var text = c.innerText;
    var iata = text.match(/([A-Z]{3})\s*-/);
    var name = c.getAttribute('data-name') || '';
    var dist = c.getAttribute('data-distance');
    var cat = c.getAttribute('data-category');
    var price = c.getAttribute('data-price');
    var eco = text.match(/Economy class\s*:\s*~?\s*([\d,]+)/);
    var bus = text.match(/Business class\s*:\s*~?\s*([\d,]+)/);
    var fir = text.match(/First class\s*:\s*~?\s*([\d,]+)/);
    var cargo = text.match(/Cargo\s*:\s*~?\s*([\d,]+)/);
    var gross = text.match(/Gross price\s*:\s*\$\s*([\d,]+)/);
    lines.push([iata?iata[1]:'',name,dist,cat,price,
      eco?eco[1]:'',bus?bus[1]:'',fir?fir[1]:'',cargo?cargo[1]:'',gross?gross[1]:''].join('|'));
  }
  return lines.join('\n');
})()"""

ROUTE_COUNT_JS = "(() => { var c = document.querySelectorAll('.hubListBox'); return c.length > 1 ? c.length - 1 : 0; })()"

AUDIT_SELECT_ALL_JS = "(() => { var s = document.querySelector('.massSelectAll'); if(s) s.click(); })()"

AUDIT_READ_COUNT_JS = "(() => { var e = document.querySelector('.massSelectedCount'); return e ? e.textContent.trim() : '0'; })()"

AUDIT_CLICK_JS = "(() => { var b = document.querySelector('.massExternalAudit'); if(b){b.click(); return 'ok';} return 'no'; })()"

AUDIT_CHECK_POPUP_JS = "(() => { var popups = document.querySelectorAll('.popupEngine'); var last = popups[popups.length-1]; if(!last) return 'none'; var btn = last.querySelector('.purchaseButton:not(.purchaseButtonRed)'); return btn ? btn.textContent.trim() : 'no_btn'; })()"

AUDIT_CONFIRM_JS = "(() => { if(typeof massNetwork !== 'undefined') massNetwork.onAudit(); })()"

AUDIT_CLOSE_POPUP_JS = "(() => { if(typeof closePopup==='function') closePopup(); })()"
