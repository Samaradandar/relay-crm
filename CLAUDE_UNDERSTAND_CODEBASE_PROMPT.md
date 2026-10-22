Mujhe is Relay CRM codebase ko samajhna hai taaki main khud files mein kaam kar sakun — poora
code padhne ki zaroorat nahi, bas ek guided map chahiye. Kam se kam tokens use karo.

Pehle ye 2 files padho (repo root mein hain):
1. CODEBASE_MAP.md — poore codebase (saare phases, backend+frontend) ka one-line-per-module map.
2. PHASE4_FILE_MAP.md — Phase 4 (customers, billing, proposals, usage_feed) ka detailed,
   file-by-file aur function-by-function map, jo abhi sabse zyada active kaam hai.

Dono files ko base bana ke mujhe batao:

1. Har module ka EK LINE mein kaam kya hai — jaise ek non-technical insaan ko samjhao.
2. Agar main kisi specific cheez mein change karna chahun (jaise "invoice edit ka rule badalna
   hai" ya "nayi alert add karni hai" ya "deal page pe field dikhani hai"), to exactly KONSI
   FILE kholu — seedha path bata do, poora module mat padho.
3. Jab bhi main koi feature/bug ke baare mein puchun:
   - Pehle check karo wo PHASE4_FILE_MAP.md mein hai ya nahi (billing/subscription/invoice/
     payment/pack/usage/proposal/customer se related).
   - Agar haan, seedha wahi se file dhoond lo.
   - Agar nahi, CODEBASE_MAP.md se module dhoondo, uska README.md padho (ek paragraph hota
     hai), phir sirf us module ke models.py/service.py/router.py mein se jo zaroori ho wahi
     padho — teeno mat padho jab tak sawaal sabko touch na kare.
   - Kisi stage ke exit-criteria/rule ka sawaal ho to seedha
     api/app/modules/pipeline/seeds/new_business.py ka CRITERIA dict dekho — kahin aur se
     guess mat karo.
4. Agar koi cheez dono map files mein nahi mili, to mujhe bata do ki ye naya/unmapped area hai,
   aur sirf tabhi us folder ko khud explore karo (aur agar possible ho to naye map mein add
   karne ka suggestion do).

Zaroori rules:
- Hinglish mein seedha, chhote jawab do. Lambi technical explanation mat do jab tak main na
  pochun.
- Code likhne ya file edit karne se pehle mujhse confirm karo.
- Jawab dene se pehle bata do kaunsi file(s) tumne padhi (transparency ke liye).
