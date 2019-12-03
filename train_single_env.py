import os
import collections
import pickle
import shutil
import csv
import gym
import numpy as np
import time
from stable_baselines import PPO2
from stable_baselines.common.policies import MlpPolicy
from stable_baselines.common.vec_env import DummyVecEnv, SubprocVecEnv
from stable_baselines.common.vec_env.vec_normalize import VecNormalize
import wandb
from tensorflow import flags
import driving_envs

FLAGS = flags.FLAGS
# PPO parameters
flags.DEFINE_integer("timesteps", 1000000, "# timesteps to train")
# Experiment related parameters
flags.DEFINE_string("name", "eff", "Name of experiment")
flags.DEFINE_boolean("is_save", True, "Saves and logs experiment data if True")
flags.DEFINE_integer("eval_save_period", 10, "how often we save state for eval")


def train(timesteps, experiment_name, is_save, eval_save_period, num_envs=1):
    """
    Trains driving agent
    :param timesteps: number of timesteps to train. 128 (n_steps) * 100
    """
    if is_save:  # create experiment name directory and wandb log
        if os.path.exists(experiment_name):
            shutil.rmtree(experiment_name)
        os.makedirs(experiment_name)
        rets_path = os.path.join(experiment_name, "eval.csv")
        wandb.save(experiment_name)

    env_fns = num_envs * [lambda: gym.make("Merging-v0")]
    env = VecNormalize(SubprocVecEnv(env_fns))
    policy = MlpPolicy
    model = PPO2(policy, env, verbose=1)
    eval_env = VecNormalize(DummyVecEnv(env_fns), training=False, norm_reward=False)

    def evaluate_debug(model, eval_dir=None):
        """
        Evaluates model on one episode of driving task. Returns mean episode reward.
        Rolls out actions according to current policy until the episode finishes (ever_done)
        """
        rets = 0.0
        obs = eval_env.reset()
        state, ever_done = None, False
        task_data = []
        while not ever_done:
            action, state = model.predict(obs, state=state, deterministic=True)
            print("\naction: ", action)
            next_obs, rewards, done, _info = eval_env.step(action)
            print("rewards: ", rewards)
            #if not is_save: eval_env.render()
            eval_env.render()
            if not ever_done:
                task_data.append([eval_env.venv.envs[0].world.state, action, rewards, done])  # append true state, not model predicted state
                rets += rewards
            ever_done = np.logical_or(ever_done, done)  # ever_done will be True when done=True
            obs = next_obs
            #if not is_save: time.sleep(.1)
            time.sleep(.1)
        if is_save:
            assert eval_dir
            with open(os.path.join(eval_dir, "task_data.pkl"), "wb") as f:
                pickle.dump(task_data, f)
        return rets

    def evaluate(model, eval_dir=None):
        """
        Evaluates model and returns mean episode reward.
        """
        # rets = 0.0
        # state, ever_done = None, False
        # task_data = []
        obs = eval_env.reset()
        rets = np.zeros(num_envs)
        state, dones = None, [False for _ in range(num_envs)]
        ever_done = np.zeros((num_envs,), dtype=np.bool)
        task_data = collections.defaultdict(list)  # Maps env_idx -> (state, action, reward, done) tuples
        while not np.all(ever_done):
            true_states = [
                inner_env.world.state for inner_env in eval_env.venv.envs
            ]
            action, state = model.predict(obs, state=state, mask=dones, deterministic=True)
            next_obs, rewards, dones, _info = eval_env.step(action)
            if not is_save: eval_env.venv.envs[0].render()

            for env_idx, data in enumerate(zip(true_states, action, rewards, dones)):
                if not ever_done[env_idx]:
                    task_data[env_idx].append(data)
                    rets[env_idx] += rewards[env_idx]
            ever_done = np.logical_or(ever_done, dones)
            obs = next_obs
            if not is_save: time.sleep(.1)
        if is_save:
            assert eval_dir
            with open(os.path.join(eval_dir, "task_data.pkl"), "wb") as f:
                pickle.dump(task_data, f)
        return np.mean(rets)

    best_ret, n_steps = -np.infty, 0

    def callback(_locals, _globals):
        """
        Calls this function every n_updates during training(I think..need to double check)
        """
        nonlocal n_steps, best_ret
        model = _locals['self']
        if (n_steps + 1) % eval_save_period == 0:
            start_eval_time = time.time()
            if is_save:
                eval_dir = os.path.join(experiment_name, "eval{}".format(n_steps))
                os.makedirs(eval_dir)
                #ret = evaluate(model, eval_dir)
                ret = evaluate_debug(model, eval_dir)
                if ret > best_ret:
                    print("Saving new best model")
                    _locals['self'].save(eval_dir + 'best_model_{}_{}.pkl'.format(n_steps, ret))
                    best_ret = ret
            else:
                ret = evaluate_debug(model)
            print("eval ret: ", ret)
            if is_save:
                wandb.log({"eval_ret": ret}, step=_locals["self"].num_timesteps)
                with open(rets_path, "a", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow([n_steps, ret])
            end_eval_time = time.time() - start_eval_time
            print("Finished evaluation in {:.2f} seconds".format(end_eval_time))
        n_steps += 1
        return True

    model.learn(total_timesteps=timesteps, callback=callback)
    #if is_save:
    #    final_dir = os.path.join(experiment_name, "eval{}".format(n_steps))
    #    os.makedirs(final_dir)
    #    ret = evaluate(model, final_dir)
    #    with open(rets_path, "a", newline="") as f:
    #        writer = csv.writer(f)
    #        writer.writerow([n_steps, ret])
    #    # model.save(experiment_name)
    #else:
    #    evaluate(model)


if __name__ == '__main__':
    if FLAGS.is_save: wandb.init(project="reward_adaptation2", sync_tensorboard=True)
    train(FLAGS.timesteps, FLAGS.name, FLAGS.is_save, FLAGS.eval_save_period)
