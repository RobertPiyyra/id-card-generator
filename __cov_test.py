import utils,os 
samples=[ 
'\u0645\u062d\u0645\u062f \u0639\u0644\u06cc', 
'\u0627\u062d\u0645\u062f \u062e\u0627\u0646', 
'\u06a9\u0627\u0644\u06cc\u0631\u06cc\u0646 \u06a9\u06cc\u06a9\u06af', 
'\u0645\u06a9\u0627\u0646 \u0646\u0645\u0628\u0631 123\u060c \u0633\u0679\u0631\u06cc\u0679 4\u060c \u062f\u0644\u0644\u06cc', 
'\u0644\u0627\u06c1\u0648\u0631\u060c \u067e\u0627\u06a9\u0633\u062a\u0627\u0646' 
] 
fonts=['arabtype.ttf','ARABIAN.TTF','ARABIA.TTF','ARB.TTF','NotoNastaliqUrdu-Regular.ttf','NotoNastaliqUrdu-Medium.ttf'] 
for s in samples: 
 t=utils.process_text_for_drawing(s,'urdu') 
 print('TXT',s.encode('unicode_escape').decode()) 
 print('SHP',t.encode('unicode_escape').decode()) 
 for fn in fonts: 
   p=os.path.join('static/fonts',fn) 
   if not os.path.exists(p): continue 
   ok=utils._font_covers_text(p,t) 
   print(' ',fn,ok) 
 print('-'*20) 
