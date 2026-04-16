import os
import re

base_path = "c:/Users/Diya Panjwani/Downloads/EXAM-main/EXAM-main/templates/"

# 1. admin_login.html
with open(base_path + "faculty_login.html", "r", encoding="utf-8") as f:
    content = f.read()
content = content.replace("Faculty Portal", "Admin Portal")
content = content.replace('action="/faculty/login"', 'action="/admin/login"')
content = content.replace("Faculty ID", "Admin ID")
content = content.replace("e.g. FAC1001", "e.g. admin")
with open(base_path + "admin_login.html", "w", encoding="utf-8") as f:
    f.write(content)

# 2. admin_dashboard.html
with open(base_path + "faculty_dashboard.html", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace("Faculty Portal", "Admin Portal")
content = content.replace('{{ faculty.full_name.split()[0] }}', '{{ username }}')
content = content.replace('{{ faculty.full_name[0].upper() }}', 'A')
content = content.replace('{{ faculty.full_name }}', '{{ username }}')

sidebar_replacement = """
  <nav>
    <a href="{{ url_for('admin_bp.admin_dashboard') }}" class="nav-a on">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
      Overview
    </a>
    <a href="{{ url_for('admin_bp.manage_users') }}" class="nav-a">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="7" r="4"/><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/></svg>
      Manage Users
    </a>
    <a href="{{ url_for('admin_bp.manage_exams') }}" class="nav-a">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
      Manage Exams
    </a>
  </nav>
"""
content = re.sub(r'<nav>.*?</nav>', sidebar_replacement, content, flags=re.DOTALL)

# Let's simplify the main content for Admin Dashboard
main_content = """
  <main class="content">
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}{% for cat, msg in messages %}
        <div class="flash flash-{{ cat }}">{{ msg }}</div>
      {% endfor %}{% endif %}
    {% endwith %}

    <div class="stats-row">
      <div class="stat"><div class="stat-top"><span class="stat-chip chip-up">Users</span></div>
        <div class="stat-val">{{ total_users }}</div><div class="stat-lbl">Total Users</div>
      </div>
      <div class="stat"><div class="stat-top"><span class="stat-chip chip-up">Exams</span></div>
        <div class="stat-val">{{ total_exams }}</div><div class="stat-lbl">Total Exams</div>
      </div>
      <div class="stat"><div class="stat-top"><span class="stat-chip chip-up">Students</span></div>
        <div class="stat-val">{{ total_students }}</div><div class="stat-lbl">Students Enrolled</div>
      </div>
      <div class="stat"><div class="stat-top"><span class="stat-chip chip-up">Faculty</span></div>
        <div class="stat-val">{{ total_faculty }}</div><div class="stat-lbl">Faculty Staff</div>
      </div>
    </div>
  </main>
"""
content = re.sub(r'<main class="content">.*?</main>', main_content, content, flags=re.DOTALL)

with open(base_path + "admin_dashboard.html", "w", encoding="utf-8") as f:
    f.write(content)

# 3. admin_users.html
admin_users_content = content.replace("admin_dashboard", "manage_users")
admin_users_content = admin_users_content.replace("Overview", "Manage Users")
users_main = """
  <main class="content">
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}{% for cat, msg in messages %}
        <div class="flash flash-{{ cat }}">{{ msg }}</div>
      {% endfor %}{% endif %}
    {% endwith %}
    <div class="panel">
      <div class="panel-hd"><div class="panel-title">System Users</div></div>
      <table class="etable">
        <thead>
          <tr>
            <th>ID</th>
            <th>Username</th>
            <th>Email</th>
            <th>Role</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
        {% for u in users %}
        <tr>
          <td>{{ u.id }}</td>
          <td>{{ u.username }}</td>
          <td>{{ u.email }}</td>
          <td>{{ u.role|capitalize }}</td>
          <td>
            <form action="{{ url_for('admin_bp.delete_user', user_id=u.id) }}" method="POST" style="display:inline;" onsubmit="return confirm('Are you sure you want to delete this user?');">
                <button type="submit" class="btn btn-primary" style="background:#dc2626;padding:4px 8px;font-size:11px;">Delete</button>
            </form>
          </td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </main>
"""
admin_users_content = re.sub(r'<main class="content">.*?</main>', users_main, admin_users_content, flags=re.DOTALL)
with open(base_path + "admin_users.html", "w", encoding="utf-8") as f:
    f.write(admin_users_content)

# 4. admin_exams.html
admin_exams_content = content.replace("admin_dashboard", "manage_exams")
admin_exams_content = admin_exams_content.replace("Overview", "Manage Exams")
exams_main = """
  <main class="content">
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}{% for cat, msg in messages %}
        <div class="flash flash-{{ cat }}">{{ msg }}</div>
      {% endfor %}{% endif %}
    {% endwith %}
    <div class="panel">
      <div class="panel-hd"><div class="panel-title">All Exams</div></div>
      <table class="etable">
        <thead>
          <tr>
            <th>Course Code</th>
            <th>Subject</th>
            <th>Exam Name</th>
            <th>Duration</th>
            <th>Total Marks</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
        {% for e in exams %}
        <tr>
          <td>{{ e.course_code }}</td>
          <td>{{ e.subject_name }}</td>
          <td>{{ e.exam_name }}</td>
          <td>{{ e.duration_minutes }} mins</td>
          <td>{{ e.total_marks }}</td>
          <td>
            <form action="{{ url_for('admin_bp.delete_exam', course_code=e.course_code) }}" method="POST" style="display:inline;" onsubmit="return confirm('Are you sure you want to delete this exam?');">
                <button type="submit" class="btn btn-primary" style="background:#dc2626;padding:4px 8px;font-size:11px;">Delete</button>
            </form>
          </td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
  </main>
"""
admin_exams_content = re.sub(r'<main class="content">.*?</main>', exams_main, admin_exams_content, flags=re.DOTALL)
with open(base_path + "admin_exams.html", "w", encoding="utf-8") as f:
    f.write(admin_exams_content)

print("Admin templates generated.")
