import re

with open("fedora_builder/core/disk_engine.py", "r") as f:
    content = f.read()

# Fix the broken sed replacements
content = content.replace('subprocess.check_output(["du", "-sm", str(rootfs)], check=True)', 'subprocess.check_output(["du", "-sm", str(rootfs)])')
content = content.replace('int(out.split()[0], check=True)', 'int(out.split()[0])')
content = content.replace('], check=True)', ']')
content = content.replace('kernel_params.split() if p != "rd.live.image"], check=True)', 'kernel_params.split() if p != "rd.live.image"])')
content = content.replace('subprocess.run(cmd, check=True)', 'subprocess.run(cmd)')

# Now do proper replacements only for self.toolchain.run_in_build_host and subprocess.run
def add_check_true(match):
    prefix = match.group(1)
    call = match.group(2)
    # Avoid double check=True
    if "check=True" in call:
        return match.group(0)
    # Check if the call ends with )
    if call.endswith(")"):
        new_call = call[:-1] + ", check=True)"
    else:
        new_call = call
    return f"{prefix}{new_call}"

content = re.sub(r'(self\.toolchain\.run_in_build_host\()([^;]+?\))', add_check_true, content)
content = re.sub(r'(subprocess\.run\()([^;]+?\))', add_check_true, content)

# But wait, what if my regex misses something? Let's just manually replace the exact strings.
