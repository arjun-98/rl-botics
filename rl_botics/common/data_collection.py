from collections import deque
import numpy as np


def rollout(env, agent, render=False, timestep_limit=1000):
    """
        Execute one episode
    """
    obs = env.reset()
    ep_rew = 0
    for t in range(timestep_limit):
        if render:
            env.render()
        action = agent.pick_action(obs)
        new_obs, rew, done, info = env.step(action)
        ep_rew += rew
        # Store transition
        transition = deque((obs, action, rew, new_obs, done))
        yield transition

        if done:
            print("Terminated after %s timesteps with reward %s" % (str(t+1), str(ep_rew)))
            break

        obs = new_obs


def get_trajectories(env, agent, render=False, max_transitions = 256):
    """
    :param env: Environment
    :param agent: Policy pi
    :return: Trajectories
             Each trajectory contains:
             [0] obs: Observation of the current state
             [1] action: Action taken at the current state
             [2] rew: Reward collected
             [3] new_obs: New observation of the next state
             [4] done: Boolean to determine if episode has completed

    """
    data = deque()
    num_transitions = 0
    while True:
        for transition in rollout(env, agent, render):
            data.append(transition)
            num_transitions += 1

        if num_transitions > max_transitions:
            break

    return data
