import subprocess
import os
from fedora_builder.utils.logger import setup_logger

logger = setup_logger("CommandRunner")

class CommandRunner:
    @staticmethod
    def run_chroot_stream(chroot_path, command, env=None, mode="real"):
        if mode == "mock":
            logger.info(f"[MOCK CHROOT] {chroot_path} - Command: {command}")
            return subprocess.CompletedProcess(args=command, returncode=0, stdout=b'', stderr=b'')
            
        cmd = ["chroot", chroot_path]
        if isinstance(command, list):
            cmd.extend(command)
        else:
            cmd.extend(["/bin/sh", "-c", command])
            
        logger.debug(f"Running chroot command: {cmd}")
        return CommandRunner._run_stream(cmd, env=env)

    @staticmethod
    def run_host_command(command, env=None, cwd=None):
        logger.debug(f"Running host command: {command}")
        return CommandRunner._run_stream(command, env=env, cwd=cwd)

    @staticmethod
    def _run_stream(cmd, env=None, cwd=None):
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
            
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=process_env,
            cwd=cwd,
            bufsize=1,
            universal_newlines=True
        )
        
        output = []
        for line in process.stdout:
            print(line, end="")
            output.append(line)
            
        process.wait()
        
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=process.returncode,
            stdout="".join(output),
            stderr=""
        )
