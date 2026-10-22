<!-- Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta. -->
# Relay CRM: Manual testing guide (Phase 1, 2, 3)

## Kaise shuru karein
- Browser (Edge/Chrome) mein kholo: **http://localhost:5180**
- Login page par "dev login" se email chuno. Password nahi hota.
- System **khali** hai: koi company, lead ya deal nahi. Sirf neeche wale 10 log, 2 teams (North, South) aur pipeline ke default stages hain. Companies aap khud dalo.
- Pehle kuch real companies **New lead** se banao (kam se kam 5-6, alag-alag users se), phir niche ke tests karo. Board, pool, profile ke numbers tabhi dikhenge.

| Kaun | Email | Role | Team |
|---|---|---|---|
| Samar | samar@relay.local | Super admin | - |
| Anita | anita@relay.local | Admin | - |
| Tara | tara@relay.local | Team Leader | North |
| Tejas | tejas@relay.local | Team Leader | South |
| Priya, Rahul | priya@ / rahul@relay.local | Sales rep (User) | North |
| Sneha, Vikram | sneha@ / vikram@relay.local | Sales rep (User) | South |

(Aur reps chahiye to Anita se Users page par khud bana lo, ye bhi test hai.)

Tip: do browser use karo (ek normal, ek Incognito), taaki do log ek saath login rahein (Phase 2 ke live tests ke liye).
Har test mein: **Karo** = kya karna hai, **Hona chahiye** = sahi result. Result alag ho to screenshot lo aur note karo.

---
# PHASE 1: Base (login, roles, approvals, audit)

**1.1 Login**
- Karo: priya@relay.local se login. **Hona chahiye:** Today page khule, upar naam dikhe.
- Karo: Logout, phir galat email (dev login mein sirf wahi 10 log hain, to galat email ke liye URL se alag koi email try karo) (`nobody@relay.local`). **Hona chahiye:** saaf error message, login nahi.
- Karo: login ke baad browser ka token hata ke (ya naya Incognito window) seedha `/board` kholo. **Hona chahiye:** login page par wapas.

**1.2 Role ke hisaab se menu**
- Priya (rep): menu mein Today, Board, New lead, Companies, Profile jaise items. **Nahi dikhna chahiye:** Users, Teams, Assignment, Pipeline, Failed jobs, Exports (agar rep ko nahi di gayi).
- Tara (Team Leader): Team, Transfers, Monitoring bhi dikhen. Users/Teams (Admin) nahi.
- Anita (Admin): Users, Teams, Assignment, Pipeline, Catalog, Failed jobs dikhen.
- Samar (Super admin): sab kuch.

**1.3 Reason (karan) maangna**
- Anita se koi important kaam karo (jaise kisi user ko deactivate). **Hona chahiye:** "Reason" dialog khule. Khali ya sirf spaces dene par aage na badhe.
- Reason dene par kaam ho jaye. Escape dabane par dialog band ho.

**1.4 Users aur Teams (Admin)**
- Anita: Users → naya user banao (reason ke saath). **Hona chahiye:** list mein dikhe.
- Tara (Team Leader) se user banane ki koshish. **Hona chahiye:** seedha nahi banta; ye **Approval request** ban jaata hai.
- Anita: Approvals → wo request approve karo. **Hona chahiye:** user ban jaye, Tara ko notification mile.
- Tara se kisi user ka rename ya team move try karo. **Hona chahiye:** refuse (permission nahi).
- Anita ko User role mein badlo bina team ke. **Hona chahiye:** rok de (team zaroori hai).

**1.5 Approvals**
- Tara ki request ko Tara khud approve nahi kar sakti. **Hona chahiye:** button na dikhe ya refuse ho.
- Do log ek hi request approve karne ki koshish karein (do browser). **Hona chahiye:** ek ko success, doosre ko "already decided".

**1.6 Notifications (bell)**
- Kuch hone par bell par number aaye. Kholne par list, "mark read" se number ghate. 400 px chhoti screen par bell panel kat-ta nahi hona chahiye.

**1.7 Data alag-alag organisation ka**
- Ye UI se nahi dikhta. Isse automatic tests cover karte hain (isolation pass hai).

---
# PHASE 2: Lead Pool (leads, duplicates, assignment, transfers)

**2.1 Naya lead banana**
- Priya (rep): New lead → Company, contact naam, phone (`9876543210`), source bharo (**Next step aur due date ab form mein nahi hain**). **Hona chahiye:** lead **assign nahi hota**. Form par message: **"Sent to your Team Leader to assign"** (koi deal page nahi khulta, kyunki lead abhi Priya ka nahi). Form ke neeche "Waiting for your Team Leader" list mein company aur automatic assignment ka time dikhe. Priya ko bell mein "Your lead is waiting for your Team Leader".
- **Pehla next step system khud lagata hai:** lead banne ke baad deal/Today par next step = pipeline ke Lead stage ka **pehla adhura criterion** (seed pipeline mein: "Basic info complete (company, contact, phone or email, source)"), due = **kal (IST)**. Aap pipeline settings mein label badlo, naya version publish karo: naye lead mein naya label dikhe, purane deals mein purana (version pinned). Teeno criteria poore hon to "Move to Qualification". API se `next_step_text` / `next_step_due_date` bhejo to wahi rakha jata hai (sirf spaces wala text = error, purani date = error).
- **Stage badalne par:** deal ko agle stage mein le jao (jaise Qualification). Agar open next step system ne khud lagaya tha to naye stage ka pehla adhura criterion ("Client's problem written down (pain point) and how our product solves it") ban jata hai, due kal. Agar aapne khud next step likha/badla tha to wo **kabhi nahi badalta**. History mein ek "next_step.set (auto)" row.
- **Rep ka lead kahan jata hai:** pehle assignment rules dekhe jate hain; rule match ho to us rule ki team ka Team Leader decide karta hai. Koi rule match na ho to **creator ki apni team** ka Team Leader (default team nahi; default team tabhi jab creator ki team ho hi nahi). Sirf wahi Team Leader ko ek notification "A rep's lead needs your decision"; doosri team ke TL ko nahi. (Vikram (South) ka lead, rule nahi: Tejas ko dikhe, Tara ko nahi.)
- Khali company / sirf spaces / phone aur email dono khali. **Hona chahiye:** field ke paas error.
- Galat website (`javascript:abc`) ya galat email. **Hona chahiye:** error, lead nahi banta.
- **Lead kisko mile (sirf Team Leader / Admin / Super admin):** form mein "Assign to" dropdown aata hai. Priya (rep) ko ye dropdown **nahi** dikhna chahiye.
  - Tara (TL): "Assign automatically (round robin / rules)" (default, reason nahi maangta) = pehle jaisa. **Hona chahiye:** rule/round robin se owner.
  - Tara: "Assign to me". **Hona chahiye:** reason maangta hai, lead Tara ke naam, method "manual", deal page khulta hai.
  - Tara: "Assign to a specific rep". **Hona chahiye:** list mein sirf North ke active reps (South ke nahi); rep chune bina Create band; reason maangta hai; lead us rep ke paas, method "manual", rep ko "New lead assigned to you" notification. Rep ka cap pura ho to bhi lead assign hota hai aur cap ki warning dikhti hai.
  - Anita / Samar (Admin / Super admin): rep list **team ke hisaab se grouped** (North, South), koi bhi active rep chun sakte hain. Anita ke liye reason zaroori, Samar ke liye optional.
  - Tara / Anita / Samar ke banaye lead **hold nahi hote**: wahi upar wala behaviour (dropdown, ya automatic).
  - History (audit) mein **ek lead.create** aur **ek pool.assign** row dikhni chahiye. Round robin ka agla number is manual pick se **nahi** badalta.
  - Kisi aur team ka rep, inactive ya doosri organisation ka user API se bhejo. **Hona chahiye:** 403/422 (kabhi 500 nahi) aur koi lead nahi banta. Rep ka `owner_id` bhejna 403.
  - Agar creator na owner hai na us team ka, to deal page ke 404 ki jagah confirmation message dikhe ("Lead created and assigned to ...").

**2.2 Duplicate rokna**
- Pehle ek company banao (jaise "Acme Pvt Ltd", website acme.com, phone 9876543210). Phir dobara naya lead banao jisme **same website** ya **same phone** ho (kisi bhi format mein: `+91 98765 43210`). **Hona chahiye:** live panel mein "blocked", Create button band.
- Milte-julte naam ki company (jaise "Acme Pvt Ltd" vs "Acme Private Limited"). **Hona chahiye:** warning "similar", "Create anyway" ke saath reason maange.
- (Tejas/Sneha ki team South mein ek company banao, phir Priya (North) se wahi company/phone daalo.) Doosri team ke lead se match karne par rep ko sirf ye dikhna chahiye: *"Company already exists, owner: X. Contact your Team Leader."* Koi link, id, ya team ka naam nahi.
- Anita (Admin): block override karo. **Hona chahiye:** **reason hamesha maangta hai**, aur account se attach ho jaye.

**2.3 Auto-assignment (round robin)**
- Priya se 3 leads banao aur Tara ke Lead Pool mein har lead par **"Auto round robin"** dabao. **Hona chahiye:** North ke reps mein barabar bantein (rotation: Priya, Rahul, Aarav jaisa; Tara ko kabhi nahi). Seedhe Tara/Anita ke banaye "automatic" leads pehle jaise turant bantein.
- **Hold window:** Anita → Settings → Assignment → "Minutes a sales rep's lead waits for its Team Leader" (default 120; 1 se 10080). Badalne par reason maangta hai aur history mein purani/nayi value dikhe; 0 ya 10081 par error. Window khatam hone par (job har 15 min chalta hai, to zyada se zyada window + 15 min) jis rep-lead par Tara ne kuch nahi kiya wo **apne aap round robin** se assign ho jata hai; history mein "lead.hold_expired" aur "lead.assigned (System)". Window chhoti (jaise 1 min) karke test kar sakte ho.
- Anita: Assignment page mein koi rep ka cap 1 karo, phir kai leads banao. **Hona chahiye:** cap pura hone par lead "waiting" mein jaye, reason saaf shabdon mein, aur Team Leader ko ek hi baar notification.
- Cap badhao. **Hona chahiye:** waiting lead ~turant assign (ya 15 min mein).
- Tara ne "Assign to a specific rep" se lead banayi ho to wo round robin nahi chalata (`assignment_method` = manual); uske baad auto leads rotation wahin se chalte hain jahan the.

**2.4 Lead Pool**
- Tara: Lead Pool kholo. **Hona chahiye:** sirf uski team ke waiting leads. Priya ko Lead Pool nahi dikhna chahiye.
- Priya ya Vikram ka rep-lead Tara/Tejas ke Pool mein: row par **"Created by Priya. Automatic round robin in 1 h 59 min"** (time ghatta rehta hai) aur **teen** button:
  1. **Give to Priya** (ek click, reason nahi): lead Priya ko, method "manual". Priya inactive ho ya doosri team ki ho to ye button band.
  2. **Pick another rep**: list mein sirf us team ke doosre active reps (creator aur doosri team nahi) → Assign. Lead us rep ko, creator ko nahi (creator ab wo deal nahi khol sakta).
  3. **Auto round robin**: lead rotation ke agle rep ko (us team ke), pointer aage badhta hai. (Pehle 1 aur 2 pointer ko nahi chhedte.)
  Teeno mein Tara ka naam "assigned by" mein, ek hi pool.assign history row, rep ko "New lead assigned to you". Dobara decide karna ya do TL ek saath: ek hi jeetega, doosre ko "no longer waiting" error. Tejas (South) Tara ki team ka lead decide **nahi** kar sakta (403). Priya Pool ya decide API nahi chala sakti (403) aur `owner_id` se bypass bhi nahi (403). Anita decide kare to reason zaroori.
- Pool se kisi ko manually assign karo. **Hona chahiye:** wo rep ke paas jaye, dono ko notification.
- Filters kaam karein. Doosre browser mein lead banao, **bina reload** pool mein dikhna chahiye.

**2.5 Transfer (team se team)**
- Tara: Transfers → "Take" ya "Give" request banao. **Hona chahiye:** Tejas aur Admin ko notification.
- Tejas comment kare, Anita approve kare. **Hona chahiye:** deal naye owner/team mein, **purane owner ka access turant gaya**.
- Reject ya cancel ke baad naya request banta hai.

**2.6 Companies aur merge**
- Companies list, search. Doosri team ki deal wali company mein deal "locked" dikhe, andar ki detail nahi.
- Admin: do duplicate companies merge karo. **Hona chahiye:** deals aur contacts ek ho jayein, purani company list mein na dikhe.

**2.7 Edit aur Undo**
- Deal edit karo (version badal jaye to conflict message). Edit ke 10 second ke andar **Undo**. **Hona chahiye:** wapas purana. Band deal (lost/won) par Undo nahi chalta.

---
# PHASE 3: Pipeline, deals, catalog, documents, exports, profiles

**3.1 Board aur stages**
- Pehle 5-6 leads banao (2.1). Priya: Board kholo. **Hona chahiye:** deals columns mein (stages), har card par company, value, health (on track / stale / at risk). Shuru mein sab Lead stage mein hote hain.
- Stage page kholo (stage ka naam click). **Hona chahiye:** us stage ke sab deals; Onboarding page par Won deals bhi dikhein.

**3.2 Deal aage badhana (proof ke saath)**
- Deal kholo → "Move" next stage. Stage ke criteria (checklist/proof) bina pure kiye move karne ki koshish. **Hona chahiye:** refuse, kaun sa proof baaki hai wo dikhe.
- **Lead stage mein** Fit check ke 4 sawal tick karo (deal Edit mein: website asli, phone sahi, client interested, company genuine), phir criterion "Fits the target customer profile" tick karo. **Hona chahiye:** Lead ke 4 criteria (basic info, company identified, contact verified, fit) pure hon to Move chale. Qualification mein fit check nahi puchha jata, wahan sirf pain point (client ki problem aur hum kaise solve karte hain) aur meeting scheduled chahiye.
- Stage page: Lead page par "Fit check" column (jaise 2/4) dikhe; Qualification page par nahi (wahan Pain point aur Meeting date).
- Skip: reason ke saath. **Hona chahiye:** audit mein likha jaye. Revision 2 mein skip karne par round 2 dikhe.

**3.3 Activity aur next step**
- Deal par activity log karo, next step set karo. **Hona chahiye:** timeline mein dikhe, Today page par due next step aaye.

**3.4 Products aur lines**
- Deal mein product line jodo, quantity, discount (jaise 12%). **Hona chahiye:** total sahi (paise mein, ₹ mein dikhe). 10% se zyada discount par approval request jaye; approval ke baad hi lage.
- Quantity bahut bada (`99999999999`). **Hona chahiye:** saaf error, 500 nahi.

**3.5 Documents**
- Proposal ya agreement banao/file upload karo. **Hona chahiye:** deal par dikhe. Sent/countersigned agreement ki file badal nahi sakti.

**3.6 Won aur Lost**
- Deal ko Onboarding mein le jao. **Hona chahiye:** deal **Won**, rep ka cap free. Onboarding mein bhi "Log activity" aur "Tick" chalein.
- Deal Lost karo (reason list se). **Hona chahiye:** board se hat jaye, Deleted/Lost mein dikhe. Delete kiya hua deal ke records rep ko nahi dikhne chahiye.

**3.7 Role ke hisaab se buttons**
- Anita ya Tara kisi aur ki deal kholein. **Hona chahiye:** Tick/Move/Skip ke buttons **nahi** dikhein (server 403 dega, isliye button hi nahi hona chahiye).

**3.8 Profile, Monitoring, Compare**
- (Kuch deals Won/Lost karne ke baad.) Priya: Profile (apne numbers). Tara: Monitoring (team ke reps), Compare (teams). **Hona chahiye:** won sirf us period ke jo IST ke hisaab se ho; Compare mein "won count" aur "value" ek hi deals se bane.

**3.9 Exports**
- Tara ya Anita: Exports → CSV, stage filter ke saath bhi. **Hona chahiye:** file download ho, error nahi. Excel mein khole to formulas jaise `=SUM(...)` text ki tarah dikhein.

**3.10 Pipeline settings aur Catalog (Admin)**
- Anita: Pipeline → naya version draft, stage/criteria badlo, publish. **Hona chahiye:** purane deals purane version par, naye deals naye par. **Onboarding stage hata ke publish karne par refuse ya saaf warning** (Won deals fansne nahi chahiye).
- Catalog: product/rate jodo, naam khali ya sirf spaces nahi chalna chahiye.

**3.11 Phone aur dark screen**
- Browser ko 400 px chouda karo. **Hona chahiye:** kisi page par side-scroll nahi. (Catalog page abhi known issue: side-scroll ho sakta hai.)

---
# Kya dhyaan se dekhna
- Koi page par **error ya sirf white screen** ho to screenshot + URL note karo.
- Kuch bhi galat data kisi doosri team ka dikhe to **turant** note karo (ye sabse gambhir hai).

# Jo abhi jaanbujhkar nahi hai
Website se lead aana, rep ka "claim" request, login rate limit, dark theme, voice/call features, production setup.
