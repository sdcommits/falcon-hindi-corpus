"""Small hand-written word lists used by language ID.

Generic function words and very common words only; nothing is taken from a
specific document. Romanised Hindi has no standard spelling, so variants are
listed (mein/me/mai/main, nahi/nahin/nhi ...). Keep entries lowercase ASCII.
"""

# Romanised-Hindi words that are (almost) never English.
HI_LATN_STRONG = frozenset("""
aaj aana aao aap aapka aapke aapki aapko aapse aaya aaye aayenge aayi abhi accha acche acchi
acha achha achhe achhi adhura agar agla agle agli apka apke apki apna apne apni aur baad baarish
baat baatein bada badal badalna bade badi badla badlein badli badlna badlo bahut bajaen bajaiye
bajana bajao bajata bajati bajaye baje bana banaen banaiye banana banao banaya banaye banayein
barish bata bataiye batana batao bataye batayein behen bhabhi bhai bhaiya bhaiyya bhej bhejein
bhejiye bhejna bhejo bhi bhool bhoolna bhot bhula bilkul bje bohot bola bole bolenge boli bolna
bolo bolta bolte bolti bura bure buri chacha chahie chahiye chahta chahte chahti chala chalana
chalao chalenge chalna chalo chota chote choti chup dadi dedo dekha dekhe dekhiye dekhna dekho
dena denge desh dhanyavaad dhanyawad dhire dhoondhein dhundein dhundh dhundo didi dijie dijiye
dikha dikhaiye dikhana dikhao dikhayein dil dohrain dohrana dohrao dohraye doosra dopahar dost
duniya dusra dvara dwaara dwara fir gaana gaane gaano galat gana gaon garmi gaya gaye gayee gayi
ghanta ghante ghar gya gyi haan haanji hafta hafte hai hain hamara hamare hamari hamein hamesha
har hata hatado hatana hatao hatayein hawa hein hoga hogi honge hoon hota hote hoti hu hua hue
hui hum hume humko hun idhar inka inke inki inko iska iske iski isko islie isliye jaa jaaen
jaana jaane jaao jaaye jaayen jabki jaha jahan jaldi jana jao jaunga jaungi jaye jayega jayegi
jayen jayenge jyada ka kaam kab kabhi kaha kahaan kahan kahe kahi kahin kaisa kaise kaisey kaisi
kar karado kardena kardijiye kardo kare karein karenge karke karna karo karta karte karti
karunga karungi karwa karwao karwayein kaun ke kehna kehta kehte kehti keliye kesa kese kesi
khaana khana khatam khol kholein kholo ki kidhar kijie kijiye kijiyega kisi kisiko kisko kitna
kitne kitni kiya kiye ko koi ksa kuch kuchh kuchhh kya kyon kyonki kyu kyuki kyun kyunki laga
lagana lagao lage lagega lagegi lagi lagta lagte lagti lekin lena lenge lie likhna likho liya
liye logon magar mahina mahine malum mat mausam mei mein meko mera mere meri mila milega milegi
milenge mili milna milta milte milti mitado mitaiye mitana mitao mitayein mjhe muje mujhe mujhko
nahee nahi nahin namaste nani napasand naya naye nayi ne nhi paani paaya padega padhna padho
padta padte padti pahla pahle paisa paise pani parso pasand pata paya pehla pehle phir pichla
pichle pichli poora pura purana purani puri pyaar pyar raat raha rahe rahi rahiye rahna raho
rakh rakhna rakho rha rhe rhi rok rokein rokiye roko ruk rukiye rukna ruko saal saare saarey
saari saath sab sabhi sabka sabke sabki sabko sabse sahi sakenge sakta sakte sakti samajh
samajhna samjha samjhao saptah sardi sare sarkar sath se shaam shaant shaanth shahar shant
shanti shukriya shuru sirf soch sochna socho subah suna sunao sundar sunein suniye sunna suno
tak tera tere teri tha thay theek thi thik thoda thodi toh tuje tujhe tujhko tum tumhara tumhare
tumhari tumhe tumko udhar unka unke unki unko uska uske uski usko utha uthao vahan vala vale
vali vapas varna vo voh waala waali waha wahan wala wale wali wapas warna wo woh yaad yaar yah
yaha yahan yar ye yeh zaroor zarur zindagi zyada
""".split())

# Words that are Hindi in Latin script but are also common English words.
# They only count once a strong hit exists.
HI_LATN_WEAK = frozenset("to me mai main he ho hi na par de do den band pe mat din dil ji ek tab jab mil log mile".split())

# English function words (evidence for 'en').
EN_STOP = frozenset("""
the a an and or but if then of to in on at by for with from as is are was were be been being am
do does did have has had will would can could should may might must shall not no yes
i you he she it we they me him her us them my your his its our their this that these those
there here what which who whom whose when where why how all any some each every both more most
other such only own same so than too very just about into over after before between under again
please set add show tell make get give take find call open play stop start turn remind
today tomorrow tonight morning evening now new need want like know think say said
""".split())

# Devanagari Hindi function words (sanity evidence, not required for 'hi').
HI_DEV = frozenset("""
है हैं था थी थे हूँ हो का की के को में से पर और यह वह नहीं कि एक भी तो जो कर लिए गया गई गए रहा रही रहे
ने इस उस कुछ हम तुम आप मैं मेरा मेरी तुम्हारा उन इन यहाँ वहाँ क्या कैसे क्यों जब तब अब लेकिन
""".split())

# Positive evidence that Devanagari text is NOT Hindi. Only words that are not Hindi.
MR_DEV = frozenset("""
आहे आहेत आहोत आणि नाही नाहीत च्या मध्ये झाले झाला झाली झाल्या करतो त्यांनी म्हणून
आम्ही माझ्या माझे माझी तुझ्या त्याच्या तिच्या मला आपल्या
""".split())
NE_DEV = frozenset("""
छ छन् छैन हुन्छ भएको भएका भयो गर्नु गर्न गरेको गर्छ गरे यो त्यो र मा लाई बाट सँग थियो थिए अनि हरु
हामी तपाईं तिमी उनी भने पनि सक्छ
""".split())

# Positive evidence for other Latin-script languages (small, high-frequency words
# that are not English). Without it Latin text with no Hindi evidence is `en`.
OTHER_LATIN = frozenset("""
el los las una por con para pero sus ya fue este esta son entre cuando muy sin sobre tambien hasta hay donde
quien desde todo nos durante todos uno les ni contra otros ese eso ante ellos esto antes algunos
les des du une et dans pas sur au avec ne ce il elle nous vous ils sont est ete etre plus mais ou comme cette ces fait
der die das und ist nicht ein eine zu den dem mit von auf fur sich auch werden aus hat dass sie nach wird bei einer
noch wie einem uber zum
yang dan di ini itu dengan untuk tidak dari dalam akan pada juga saya karena ada mereka lebih sudah bisa atau oleh
nao uma os do da em mais como foi ao ele das tem seu sua ser quando muito ha nos ja esta eu tambem so pelo pela
ate isso ela era depois sem mesmo aos ter seus quem nas esse eles estao voce tinha foram essa num nem suas meu
""".split())
