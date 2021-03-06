import tensorflow as tf
import tensorflow_probability as tfp
import numpy as np
import random
import matplotlib.pyplot as plt
from keras.optimizers import Adam
from rl_botics.common.approximators import *
from rl_botics.common.data_collection import *
from rl_botics.common.policies import *
from rl_botics.common.utils import *
from rl_botics.common.plotter import *
import hyperparameters as h
from utils import *


class TRPO:
    def __init__(self, args, sess, env):
        """
        Initialize TRPO class
        """
        self.sess = sess
        self.env = env
        try:
            self.obs_dim = self.env.observation_space.shape[0]
        except:
            self.obs_dim = self.env.observation_space.n
        open('/tmp/rl_log.txt', 'w').close()
        if args.env == 'Rock-v0':
            self.obs_dim = 1
        self.act_dim = self.env.action_space.n
        self.render = args.render
        self.env_continuous = False

        # Hyperparameters
        self.gamma = args.gamma
        self.maxiter = args.maxiter
        self.cg_damping = args.cg_damping
        self.batch_size = args.batch_size
        self.kl_bound = args.kl_bound
        self.min_trans_per_iter = args.min_trans_per_iter

        # Parameters for the policy network
        self.pi_sizes = h.pi_sizes + [self.act_dim]
        self.pi_activations = h.pi_activations + ['relu']
        self.pi_layer_types = h.pi_layer_types + ['dense']
        self.pi_batch_size = h.pi_batch_size
        self.pi_optimizer = tf.train.AdamOptimizer(learning_rate=h.pi_lr)

        # Parameters for the value network
        self.v_sizes = h.v_sizes
        self.v_activations = h.v_activations
        self.v_layer_types = h.v_layer_types
        self.v_batch_sizes = h.v_batch_sizes
        self.v_optimizer = tf.train.AdamOptimizer(learning_rate=h.v_lr)

        # Build Tensorflow graph
        self._build_graph()
        self._init_session()

    def _build_graph(self):
        """Build Tensorflow graph"""
        self._init_placeholders()
        self._build_policy()
        self._build_value_function()
        self._loss()
        self.init = tf.global_variables_initializer()

    def _init_placeholders(self):
        """
            Define Tensorflow placeholders
        """
        # Observations, actions, advantages
        self.obs = tf.placeholder(dtype=tf.float32, shape=[None, self.obs_dim], name='obs')
        self.act = tf.placeholder(dtype=tf.float32, shape=[None, 1], name='act')
        self.adv = tf.placeholder(dtype=tf.float32, shape=[None, 1], name='adv')

        # Policy old log prob and action logits (ouput of neural net)
        self.old_log_probs = tf.placeholder(dtype=tf.float32, shape=[None, 1], name='old_log_probs')
        self.old_act_logits = tf.placeholder(dtype=tf.float32, shape=[None, self.act_dim], name='old_act_logits')

        # Target for value function
        self.v_targ = tf.placeholder(dtype=tf.float32, shape=[None, 1], name='target_values')

    def _build_policy(self):
        """
            Build Policy
        """
        self.policy = MlpSoftmaxPolicy(self.sess,
                                       self.obs,
                                       self.pi_sizes,
                                       self.pi_activations,
                                       self.pi_layer_types,
                                       self.pi_batch_size,
                                       )
        print("\nPolicy model: ")
        print(self.policy.print_model_summary())
        self.old_policy = tfp.distributions.Categorical(self.old_act_logits)


    def _build_value_function(self):
        """
            Value function graph
        """
        self.value = MLP(self.sess,
                         self.obs,
                         self.v_sizes,
                         self.v_activations,
                         self.v_layer_types,
                         self.v_batch_sizes,
                         'value'
                         )
        self.v_loss = tf.losses.mean_squared_error(self.value.output, self.v_targ)
        self.v_train_step = self.v_optimizer.minimize(self.v_loss)

        print("\nValue model: ")
        print(self.value.print_model_summary())

    def _loss(self):
        """
            Build TRPO loss Tensorflow computation graph
        """
        # Log probabilities of new and old actions
        prob_ratio = tf.exp(self.policy.log_prob - self.old_log_probs)
        # prob_ratio = tf.exp(self.policy.log_prob - self.old_policy.log_prob(self.act))

        # Policy parameters
        self.params = self.policy.vars

        # Surrogate Loss
        self.surrogate_loss = -tf.reduce_mean(tf.multiply(prob_ratio, self.adv))
        self.pg = flatgrad(self.surrogate_loss, self.params)

        # KL divergence, entropy, surrogate loss
        # self.old_policy = tfp.distributions.Categorical(self.old_act_logits)
        self.kl = self.old_policy.kl_divergence(self.policy.act_dist)

        # Entropy
        self.entropy = self.policy.entropy

        # All losses
        self.losses = [self.surrogate_loss, self.kl, self.entropy]

        # Compute Gradient Vector Product and Hessian Vector Product
        self.shapes = [list(param.shape) for param in self.params]
        self.size_params = np.sum([np.prod(shape) for shape in self.shapes])
        self.flat_tangents = tf.placeholder(tf.float32, (self.size_params,), name='flat_tangents')

        # Compute gradients of KL wrt policy parameters
        grads = tf.gradients(self.kl, self.params)
        tangents = unflatten_params(self.flat_tangents, self.shapes)

        # Gradient Vector Product
        gvp = tf.add_n([tf.reduce_sum(g * tangent) for (g, tangent) in zip(grads, tangents)])
        # Fisher Vector Product (Hessian Vector Product)
        self.hvp = flatgrad(gvp, self.params)

        # Update operations - reshape flat parameters
        self.flat_params = tf.concat([tf.reshape(param, [-1]) for param in self.params], axis=0)
        self.flat_params_ph = tf.placeholder(tf.float32, (self.size_params,))
        self.param_update = []
        start = 0
        assert len(self.params) == len(self.shapes), "Wrong shapes."

        # Update policy parameters
        for i, shape in enumerate(self.shapes):
            size = np.prod(shape)
            param = tf.reshape(self.flat_params_ph[start:start + size], shape)
            self.param_update.append(self.params[i].assign(param))
            start += size

        assert start == self.size_params, "Wrong shapes."

    def _init_session(self):
        """
            Initialize tensorflow graph
        """
        self.sess.run(self.init)

    def get_flat_params(self):
        """
            Retrieve policy parameters
            :return: Flat parameters
        """
        return self.sess.run(self.flat_params)

    def set_flat_params(self, params):
        """
            Update policy parameters.
            :param params: New policy parameters required to update policy
        """
        feed_dict = {self.flat_params_ph: params}
        self.sess.run(self.param_update, feed_dict=feed_dict)

    def update_policy(self, feed_dict):
        """
            Update policy parameters
            :param feed_dict: Dictionary to feed into TensorFlow graph
        """

        def get_pg():
            return self.sess.run(self.pg, feed_dict)

        def get_hvp(p):
            feed_dict[self.flat_tangents] = p
            return self.sess.run(self.hvp, feed_dict) + self.cg_damping * p

        def get_loss(params):
            self.set_flat_params(params)
            return self.sess.run(self.losses, feed_dict)

        pg = get_pg()  # vanilla gradient
        if np.allclose(pg, 0):
            print("Got zero gradient. Not updating.")
            return

        # Get previous parameters
        prev_params = self.get_flat_params()
        loss_before = get_loss(prev_params)
        surr_before = np.mean(loss_before[0])

        # Compute Natural Gradient Direction using Conjugate Gradient Method
        stepdir = cg(f_Ax=get_hvp, b=-pg)
        step_size = 1.0
        shs = 0.5 * stepdir.dot(get_hvp(stepdir))
        lm = np.sqrt(shs / self.kl_bound)
        fullstep = stepdir / lm
        expected_improve_rate = -pg.dot(stepdir) / lm

        # Perform Linesearch to rescale update stepsize
        for itr in range(10):
            new_params = prev_params + fullstep * step_size
            surr_loss, kl, ent = get_loss(new_params)
            mean_kl = np.mean(kl)
            if mean_kl < 0:
                print(mean_kl)
            surr_loss = np.mean(surr_loss)
            improve = surr_loss - surr_before
            expected_improve = expected_improve_rate * step_size
            ratio = improve / expected_improve
            if mean_kl > self.kl_bound * 1.5:
                print("KL bound exceeded.")
            elif improve > 0:
                print("Surrogate Loss didn't improve")
            else:
                break
            step_size *= .5
        else:
            print("Failed to update. Keeping old parameters")
            self.set_flat_params(prev_params)

    def update_value(self, prev_feed_dict):
        """
            Update value function
            :param prev_feed_dict: Processed data from previous iteration (to avoid overfitting)
        """
        # TODO: train in epochs and batches
        feed_dict = {self.obs: prev_feed_dict[self.obs],
                     self.v_targ: prev_feed_dict[self.adv]
                    }
        # for epoch in range(self.v_epochs):
        #     for batch in range(self.v_batch_sizes):

        self.v_train_step.run(feed_dict)

    def process_paths(self, paths):
        """
            Process data

            :param paths: Obtain unprocessed data from training
            :return: feed_dict: Dict required for neural network training
        """
        paths = np.asarray(paths)

        # Average reward for iteration
        tot_rew = np.sum(paths[:,2])
        ep_count = np.sum(paths[:,-1])
        avg_rew = tot_rew / ep_count
        filename = '/tmp/rl_log.txt'
        with open(filename, 'a') as f:
            f.write("\n%d" % (avg_rew))
            print("Average reward: ", avg_rew)

        # Process paths
        if self.obs_dim>1:
            obs = np.concatenate(paths[:, 0]).reshape(-1, self.obs_dim)
            new_obs = np.concatenate(paths[:, 3]).reshape(-1, self.obs_dim)
        else:
            obs = paths[:, 0].reshape(-1, self.obs_dim)
            new_obs = paths[:, 3].reshape(-1, self.obs_dim)
        act = paths[:, 1].reshape(-1,1)

        # Computed expected return, values and advantages
        expected_return = get_expected_return(paths, self.gamma)
        values = self.value.predict(obs)
        adv = expected_return-values

        # Generate feed_dict with data
        feed_dict = {self.obs: obs,
                     self.act: act,
                     self.adv: adv,
                     self.old_log_probs: self.policy.get_log_prob(obs, act),
                     self.old_act_logits: self.policy.get_old_act_logits(obs),
                     self.policy.act: act}
        return feed_dict

    def train(self):
        """
            Train using TRPO algorithm
        """
        paths = get_trajectories(self.env, self.policy, self.render, self.min_trans_per_iter)
        dct = self.process_paths(paths)
        self.update_policy(dct)
        prev_dct = dct

        for itr in range(self.maxiter):
            paths = get_trajectories(self.env, self.policy, self.render, self.min_trans_per_iter)
            dct = self.process_paths(paths)

            # Update Policy
            self.update_policy(dct)

            # Update value function
            self.update_value(prev_dct)

            # Update trajectories
            prev_dct = dct

            # TODO: Log data

        self.sess.close()

    def print_results(self):
        """
            Plot the results
        """
        # TODO: Finish this section
        plot("TRPO")
        return
