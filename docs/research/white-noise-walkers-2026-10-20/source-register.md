---
layout: default
title: Register
published: false
---

<div class="page-content">
    <h1>Register Your Team</h1>
    <p>Registration for Perpetual Alpha is open! Gather your team of 1 to 4 members and enroll using the form below. At least one team member must be available to attend ICAIF 2026 in Milan in person.</p>

    <div class="glass-card" style="margin-top: 2rem;">
        <form action="https://forminit.com/f/eli15fwqfms" method="POST" id="registration-form">
            <div style="margin-bottom: 2rem;">
                <label for="team-name" style="font-weight: bold; display: block; margin-bottom: 0.5rem;">Team Name</label>
                <input type="text" id="team-name" name="fi-text-team_name" maxlength="50" required placeholder="Required" style="width: 100%; max-width: 400px; padding: 0.5rem; border-radius: 4px; border: 1px solid var(--surface-border); background: rgba(255,255,255,0.05); color: #fff;">
            </div>
            <p style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 1rem;"><em>Note: The first member listed will act as the corresponding member for the team.</em></p>
            <div style="overflow-x: auto;">
                <table class="registration-table">
                    <thead>
                        <tr>
                            <th></th>
                            <th>Name</th>
                            <th>Surname</th>
                            <th>Email</th>
                            <th style="text-align: center;">Affiliation<br>(Optional)</th>
                        </tr>
                    </thead>
                    <tbody>
                        <!-- Member 1 (Required) -->
                        <tr>
                            <td style="font-weight: bold; color: var(--accent-color);">1)</td>
                            <td><input type="text" name="fi-sender-firstName" maxlength="25" required placeholder="Required"></td>
                            <td><input type="text" name="fi-sender-lastName" maxlength="25" required placeholder="Required"></td>
                            <td><input type="email" name="fi-sender-email" maxlength="50" required placeholder="Required" pattern="^[^@\s]+@[^@\s]+\.[^@\s]+$"></td>
                            <td><input type="text" name="fi-text-member1_affiliation" maxlength="100"></td>
                        </tr>
                        <!-- Member 2 -->
                        <tr>
                            <td style="font-weight: bold; color: var(--text-secondary);">2)</td>
                            <td><input type="text" name="fi-text-member2_name" maxlength="25"></td>
                            <td><input type="text" name="fi-text-member2_surname" maxlength="25"></td>
                            <td><input type="email" name="fi-email-member2_email" maxlength="50" pattern="^[^@\s]+@[^@\s]+\.[^@\s]+$"></td>
                            <td><input type="text" name="fi-text-member2_affiliation" maxlength="100"></td>
                        </tr>
                        <!-- Member 3 -->
                        <tr>
                            <td style="font-weight: bold; color: var(--text-secondary);">3)</td>
                            <td><input type="text" name="fi-text-member3_name" maxlength="25"></td>
                            <td><input type="text" name="fi-text-member3_surname" maxlength="25"></td>
                            <td><input type="email" name="fi-email-member3_email" maxlength="50" pattern="^[^@\s]+@[^@\s]+\.[^@\s]+$"></td>
                            <td><input type="text" name="fi-text-member3_affiliation" maxlength="100"></td>
                        </tr>
                        <!-- Member 4 -->
                        <tr>
                            <td style="font-weight: bold; color: var(--text-secondary);">4)</td>
                            <td><input type="text" name="fi-text-member4_name" maxlength="25"></td>
                            <td><input type="text" name="fi-text-member4_surname" maxlength="25"></td>
                            <td><input type="email" name="fi-email-member4_email" maxlength="50" pattern="^[^@\s]+@[^@\s]+\.[^@\s]+$"></td>
                            <td><input type="text" name="fi-text-member4_affiliation" maxlength="100"></td>
                        </tr>
                    </tbody>
                </table>
            </div>
            
            <div style="margin-top: 2rem; text-align: center;">
                <div id="submit-wrapper" style="display: inline-block;">
                    <button type="submit" class="btn" id="submit-btn">Submit Registration</button>
                </div>
            </div>
        </form>
    </div>

    <h2 style="margin-top: 4rem;">Questions?</h2>
    <p>Questions about the competition, eligibility, or technical requirements? Reach out to the organizers — we're happy to help.</p>
    <p><strong>Contact Email:</strong> <a href="mailto:{{ site.email }}">{{ site.email }}</a></p>
</div>

<script>

document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('registration-form');
    const submitBtn = document.getElementById('submit-btn');
    const submitWrapper = document.getElementById('submit-wrapper');

    const rows = [
        {
            name: form.querySelector(`[name="fi-sender-firstName"]`),
            surname: form.querySelector(`[name="fi-sender-lastName"]`),
            email: form.querySelector(`[name="fi-sender-email"]`),
            affiliation: form.querySelector(`[name="fi-text-member1_affiliation"]`)
        },
        ...[2, 3, 4].map(i => ({
            name: form.querySelector(`[name="fi-text-member${i}_name"]`),
            surname: form.querySelector(`[name="fi-text-member${i}_surname"]`),
            email: form.querySelector(`[name="fi-email-member${i}_email"]`),
            affiliation: form.querySelector(`[name="fi-text-member${i}_affiliation"]`)
        }))
    ];

    function isRowValid(row) {
        return row.name.value.trim() !== '' && 
               row.surname.value.trim() !== '' && 
               row.email.value.trim() !== '' && 
               row.email.checkValidity();
    }

    const enableRow = (row, enable) => {
        row.name.disabled = !enable;
        row.surname.disabled = !enable;
        row.email.disabled = !enable;
        row.affiliation.disabled = !enable;
        
        let tooltipMsg = enable ? "" : "Add previous participants info before";
        row.name.parentNode.title = tooltipMsg;
        row.surname.parentNode.title = tooltipMsg;
        row.email.parentNode.title = tooltipMsg;
        row.affiliation.parentNode.title = tooltipMsg;
        
        if (!enable) {
            row.name.value = '';
            row.surname.value = '';
            row.email.value = '';
            row.affiliation.value = '';
        }
    };

    function updateFormState() {
        let row1Valid = isRowValid(rows[0]);
        let row2Valid = isRowValid(rows[1]);
        let row3Valid = isRowValid(rows[2]);
        let row4Valid = isRowValid(rows[3]);

        // Rule 5: Row n activated if row n-1 is completed
        enableRow(rows[1], row1Valid);
        enableRow(rows[2], row2Valid);
        enableRow(rows[3], row3Valid);

        // Check Team Name validity
        let teamNameInput = document.getElementById('team-name');
        let teamNameValid = teamNameInput.value.trim() !== '';

        // Rule 6: Submit button enabled if row 1 is valid AND team name is valid
        submitBtn.disabled = !(row1Valid && teamNameValid);
        
        if (submitBtn.disabled) {
            submitBtn.style.opacity = '0.5';
            submitBtn.style.pointerEvents = 'none';
            submitWrapper.style.cursor = 'not-allowed';
            submitWrapper.title = "Complete Team Name and Team Leader info to submit";
        } else {
            submitBtn.style.opacity = '1';
            submitBtn.style.pointerEvents = 'auto';
            submitWrapper.style.cursor = 'pointer';
            submitWrapper.title = "";
        }
    }

    form.querySelectorAll('input').forEach(input => {
        input.addEventListener('input', updateFormState);
        input.addEventListener('change', updateFormState);
    });

    updateFormState();
});
</script>
