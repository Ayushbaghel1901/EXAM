import sqlite3

def seed_large_question_bank():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    
    # Ensure Alice (id 1) is assigned Operating System, DSA, DBMS.
    # From seed_exam_data.py, alice has CS401 (DBMS), CS501 (CN). bob has CS301 (DSA), CS601 (AI).
    # Since we want to use existing subjects if possible, we'll fetch them or create them.
    
    cursor.execute("SELECT id FROM faculty_details LIMIT 1")
    alice_row = cursor.fetchone()
    if not alice_row:
        print("Please run seed_exam_data.py first to create a faculty.")
        conn.close()
        return
    alice_id = alice_row[0]

    # Create OS Subject if it doesn't exist
    cursor.execute("SELECT id FROM subjects WHERE subject_code = 'CS402'")
    os_row = cursor.fetchone()
    if not os_row:
        cursor.execute("INSERT INTO subjects (subject_code, subject_name, branch, semester, faculty_id) VALUES (?, ?, ?, ?, ?)", 
                       ("CS402", "Operating System", "CS", 4, alice_id))
        os_id = cursor.lastrowid
    else:
        os_id = os_row[0]
        
    # Get DBMS Subject ID
    cursor.execute("SELECT id FROM subjects WHERE subject_name = 'DBMS'")
    dbms_row = cursor.fetchone()
    dbms_id = dbms_row[0] if dbms_row else 1
    
    # Get DSA Subject ID 
    cursor.execute("SELECT id FROM subjects WHERE subject_name = 'DSA'")
    dsa_row = cursor.fetchone()
    dsa_id = dsa_row[0] if dsa_row else 1
    
    subjects_to_populate = [
        {"id": dbms_id, "name": "DBMS"},
        {"id": dsa_id, "name": "DSA"},
        {"id": os_id, "name": "Operating System"}
    ]
    
    # 200 questions each (100 easy, 60 medium, 40 hard)
    difficulties = ["easy"] * 100 + ["medium"] * 60 + ["hard"] * 40
    
    total_added = 0
    for subj in subjects_to_populate:
        sid = subj["id"]
        sname = subj["name"]
        print(f"Generating 200 questions for {sname}...")
        
        for i, diff in enumerate(difficulties):
            q_num = i + 1
            question_text = f"{sname} Question #{q_num}: Which of the following is a key concept related to this {diff} difficulty topic?"
            
            cursor.execute("INSERT INTO questions (subject_id, question_text, question_type, marks, difficulty) VALUES (?, ?, 'MCQ', 1, ?)", 
                           (sid, question_text, diff))
            qid = cursor.lastrowid
            
            # Options (1 correct, 3 wrong)
            cursor.execute("INSERT INTO options (question_id, option_text, is_correct) VALUES (?, ?, 1)", (qid, f"Correct Option for Q{q_num}"))
            cursor.execute("INSERT INTO options (question_id, option_text, is_correct) VALUES (?, ?, 0)", (qid, f"Distractor A for Q{q_num}"))
            cursor.execute("INSERT INTO options (question_id, option_text, is_correct) VALUES (?, ?, 0)", (qid, f"Distractor B for Q{q_num}"))
            cursor.execute("INSERT INTO options (question_id, option_text, is_correct) VALUES (?, ?, 0)", (qid, f"Distractor C for Q{q_num}"))
            total_added += 1

    conn.commit()
    conn.close()
    print(f"Successfully generated {total_added} questions total.")

if __name__ == "__main__":
    seed_large_question_bank()
